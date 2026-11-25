import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "../App.tsx";
import { jsonResponse, renderWithQuery, waitForPolls } from "../test-utils.tsx";

const PASSWORD = "correct horse battery staple";
const LOGIN_ID = "login-token";
const SERVER = "https://vault.example.test";
const EMAIL = "user@example.test";

const lockedLoggedOut = {
  state: "locked",
  mcpEnabled: false,
  bwLogin: { loggedIn: false, server: SERVER, email: EMAIL },
};

let logSpy: ReturnType<typeof vi.spyOn>;

// The page polls the CLI's output every 500 ms: the tests move a fake clock instead of waiting.
beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  localStorage.clear();
  sessionStorage.clear();
  logSpy = vi.spyOn(console, "log");
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

type Route = (init: RequestInit) => Response;

// A fake `bw login` behind the backend routes: tests append output and end it.
function fakeCli(routes: Record<string, Route> = {}) {
  const cli = {
    output: "",
    running: true,
    exitCode: null as number | null,
    print(text: string) {
      cli.output += text;
    },
    exit(code: number) {
      cli.running = false;
      cli.exitCode = code;
    },
  };
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = String(input);
      const method = init.method ?? "GET";
      const prefix = "/api/bw-login/output?after=";
      if (method === "GET" && url.startsWith(prefix)) {
        const after = Number(url.slice(prefix.length));
        return jsonResponse(200, {
          text: cli.output.slice(after),
          next: cli.output.length,
          running: cli.running,
          exitCode: cli.exitCode,
        });
      }
      const all: Record<string, Route> = {
        // a backend without the gateway, the account form is AccountPage.test.tsx
        "GET /gateway/account": () =>
          jsonResponse(404, { detail: "Not Found" }),
        "GET /api/session": () => jsonResponse(200, lockedLoggedOut),
        "POST /api/bw-login": () => jsonResponse(200, { loginId: LOGIN_ID }),
        "POST /api/bw-login/input": () => jsonResponse(204),
        "DELETE /api/bw-login": () => jsonResponse(204),
        ...routes,
      };
      const route = all[`${method} ${url}`];
      if (!route) {
        throw new Error(`Unexpected fetch: ${method} ${url}`);
      }
      return route(init);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { cli, fetchMock };
}

function callsTo(
  fetchMock: ReturnType<typeof vi.fn>,
  method: string,
  url: string,
): RequestInit[] {
  return fetchMock.mock.calls
    .filter(
      ([input, init]) =>
        String(input) === url &&
        ((init as RequestInit | undefined)?.method ?? "GET") === method,
    )
    .map(([, init]) => init as RequestInit);
}

function loginHeader(init: RequestInit): string | undefined {
  return (init.headers as Record<string, string>)["X-Bwtidy-Login"];
}

function expectPasswordNotLeaked() {
  const stored = [localStorage, sessionStorage].flatMap((storage) =>
    Object.keys(storage).map((key) => `${key}=${storage.getItem(key)}`),
  );
  expect(stored.join("\n")).not.toContain(PASSWORD);
  expect(window.location.href).not.toContain(encodeURIComponent(PASSWORD));
  expect(JSON.stringify(logSpy.mock.calls)).not.toContain(PASSWORD);
}

async function startLogin() {
  const field = await screen.findByLabelText(/Master password/);
  fireEvent.change(field, { target: { value: PASSWORD } });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  return screen.findByLabelText("Answer to the CLI");
}

test("prefills server and email and focuses the password field", async () => {
  fakeCli();
  renderWithQuery(<App />);

  const password = await screen.findByLabelText(/Master password/);
  expect(password).toHaveFocus();
  expect(screen.getByLabelText(/Server/)).toHaveValue(SERVER);
  expect(screen.getByLabelText(/Email/)).toHaveValue(EMAIL);
  expect(
    screen.queryByRole("button", { name: "Unlock" }),
  ).not.toBeInTheDocument();
});

test("prefills server and email from what the browser remembers over what the session sends", async () => {
  localStorage.setItem(
    "bwtidy.accountForm",
    JSON.stringify({
      server: "https://typed.example.test",
      email: "typed@example.test",
    }),
  );
  localStorage.setItem(
    "bwtidy.account",
    JSON.stringify({
      server: "https://used.example.test",
      email: "used@example.test",
    }),
  );
  fakeCli();
  renderWithQuery(<App />);

  await screen.findByLabelText(/Master password/);
  expect(screen.getByLabelText(/Server/)).toHaveValue(
    "https://typed.example.test",
  );
  expect(screen.getByLabelText(/Email/)).toHaveValue("typed@example.test");
});

test("prefills from the account in use when no form was typed", async () => {
  localStorage.setItem(
    "bwtidy.account",
    JSON.stringify({
      server: "https://used.example.test",
      email: "used@example.test",
    }),
  );
  fakeCli();
  renderWithQuery(<App />);

  await screen.findByLabelText(/Master password/);
  expect(screen.getByLabelText(/Server/)).toHaveValue(
    "https://used.example.test",
  );
  expect(screen.getByLabelText(/Email/)).toHaveValue("used@example.test");
});

test("a fixed account ignores what the browser remembers", async () => {
  localStorage.setItem(
    "bwtidy.accountForm",
    JSON.stringify({
      server: "https://typed.example.test",
      email: "typed@example.test",
    }),
  );
  fakeCli({
    "GET /api/session": () =>
      jsonResponse(200, {
        ...lockedLoggedOut,
        bwLogin: { ...lockedLoggedOut.bwLogin, accountFixed: true },
      }),
  });
  renderWithQuery(<App />);

  await screen.findByLabelText(/Master password/);
  expect(screen.getByLabelText(/Server/)).toHaveValue(SERVER);
  expect(screen.getByLabelText(/Email/)).toHaveValue(EMAIL);
});

test("log in writes the typed server and email to localStorage, never the password", async () => {
  fakeCli();
  renderWithQuery(<App />);
  fireEvent.change(await screen.findByLabelText(/Server/), {
    target: { value: " https://typed.example.test " },
  });
  fireEvent.change(screen.getByLabelText(/Email/), {
    target: { value: "typed@example.test" },
  });
  await startLogin();

  expect(
    JSON.parse(localStorage.getItem("bwtidy.accountForm") ?? "null"),
  ).toEqual({
    server: "https://typed.example.test",
    email: "typed@example.test",
  });
  expectPasswordNotLeaked();
});

test("server and email are editable unless the account is fixed", async () => {
  fakeCli();
  renderWithQuery(<App />);
  await screen.findByLabelText(/Master password/);
  expect(screen.getByLabelText(/Server/)).not.toHaveAttribute("readonly");
  expect(screen.getByLabelText(/Email/)).not.toHaveAttribute("readonly");
});

test("server and email are read-only for a worker whose account is fixed", async () => {
  const { fetchMock } = fakeCli({
    "GET /api/session": () =>
      jsonResponse(200, {
        ...lockedLoggedOut,
        bwLogin: { ...lockedLoggedOut.bwLogin, accountFixed: true },
      }),
  });
  renderWithQuery(<App />);
  await startLogin();

  const server = screen.getByLabelText(/Server/);
  const email = screen.getByLabelText(/Email/);
  expect(server).toHaveValue(SERVER);
  expect(email).toHaveValue(EMAIL);
  expect(callsTo(fetchMock, "POST", "/api/bw-login")[0]?.body).toBe(
    JSON.stringify({ server: SERVER, email: EMAIL, password: PASSWORD }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  await waitFor(() => expect(screen.getByLabelText(/Server/)).toBeEnabled());
  expect(screen.getByLabelText(/Server/)).toHaveAttribute("readonly");
  expect(screen.getByLabelText(/Email/)).toHaveAttribute("readonly");
});

test("Log in stays disabled while the server or the email is empty", async () => {
  const { fetchMock } = fakeCli({
    "GET /api/session": () =>
      jsonResponse(200, {
        ...lockedLoggedOut,
        bwLogin: { ...lockedLoggedOut.bwLogin, email: "" },
      }),
  });
  renderWithQuery(<App />);
  const password = await screen.findByLabelText(/Master password/);
  fireEvent.change(password, { target: { value: PASSWORD } });
  const logIn = screen.getByRole("button", { name: "Log in" });
  expect(logIn).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Email/), {
    target: { value: "   " },
  });
  expect(logIn).toBeDisabled();
  fireEvent.submit(password);
  expect(callsTo(fetchMock, "POST", "/api/bw-login")).toHaveLength(0);

  fireEvent.change(screen.getByLabelText(/Email/), {
    target: { value: ` ${EMAIL} ` },
  });
  fireEvent.change(screen.getByLabelText(/Server/), { target: { value: " " } });
  expect(logIn).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/Server/), {
    target: { value: SERVER },
  });
  expect(logIn).toBeEnabled();
  fireEvent.click(logIn);
  await waitFor(() =>
    expect(callsTo(fetchMock, "POST", "/api/bw-login")).toHaveLength(1),
  );
  expect(callsTo(fetchMock, "POST", "/api/bw-login")[0]?.body).toBe(
    JSON.stringify({ server: SERVER, email: EMAIL, password: PASSWORD }),
  );
});

test("log in posts server, email and password and shows the CLI output", async () => {
  const { cli, fetchMock } = fakeCli();
  cli.print("? Two-step login code: ");
  renderWithQuery(<App />);
  fireEvent.change(await screen.findByLabelText(/Email/), {
    target: { value: "other@example.test" },
  });
  await startLogin();

  expect(callsTo(fetchMock, "POST", "/api/bw-login")[0]?.body).toBe(
    JSON.stringify({
      server: SERVER,
      email: "other@example.test",
      password: PASSWORD,
    }),
  );
  const console_ = await screen.findByRole("log", {
    name: "Bitwarden CLI output",
  });
  await waitFor(() =>
    expect(console_).toHaveTextContent("Two-step login code:"),
  );
  expect(
    loginHeader(callsTo(fetchMock, "GET", "/api/bw-login/output?after=0")[0]!),
  ).toBe(LOGIN_ID);

  cli.print("\nmore output");
  await waitForPolls(() => expect(console_).toHaveTextContent("more output"));
  expect(screen.getByLabelText(/Master password/)).toHaveValue("");
  expect(screen.getByLabelText(/Master password/)).toBeDisabled();
  expectPasswordNotLeaked();
});

test("Enter and Send post the typed text and clear the field", async () => {
  const { fetchMock } = fakeCli();
  renderWithQuery(<App />);
  const input = await startLogin();

  fireEvent.change(input, { target: { value: "123456" } });
  fireEvent.submit(input);
  await waitFor(() =>
    expect(callsTo(fetchMock, "POST", "/api/bw-login/input")).toHaveLength(1),
  );
  expect(input).toHaveValue("");

  fireEvent.change(input, { target: { value: "y" } });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(callsTo(fetchMock, "POST", "/api/bw-login/input")).toHaveLength(2),
  );
  expect(input).toHaveValue("");

  const sent = callsTo(fetchMock, "POST", "/api/bw-login/input");
  expect(sent.map((init) => init.body)).toEqual([
    JSON.stringify({ text: "123456" }),
    JSON.stringify({ text: "y" }),
  ]);
  expect(sent.map(loginHeader)).toEqual([LOGIN_ID, LOGIN_ID]);
});

test("Send password asks the backend to type the kept password", async () => {
  const { fetchMock } = fakeCli();
  renderWithQuery(<App />);
  await startLogin();

  fireEvent.click(screen.getByRole("button", { name: "Send password" }));

  await waitFor(() =>
    expect(callsTo(fetchMock, "POST", "/api/bw-login/input")).toHaveLength(1),
  );
  const [sent] = callsTo(fetchMock, "POST", "/api/bw-login/input");
  expect(sent?.body).toBe(JSON.stringify({ password: true }));
  const afterStart = fetchMock.mock.calls.filter(
    ([input]) => String(input) !== "/api/bw-login",
  );
  expect(JSON.stringify(afterStart).includes(PASSWORD)).toBe(false);
});

test("exit code 0 finishes the login and shows the shell", async () => {
  const unlocked = {
    state: "unlocked",
    mcpEnabled: false,
    bwLogin: { ...lockedLoggedOut.bwLogin, loggedIn: true },
  };
  let finished = false;
  const { cli, fetchMock } = fakeCli({
    "POST /api/bw-login/finish": () => {
      finished = true;
      return jsonResponse(200, unlocked);
    },
    // the session as the backend answers it once the login finished
    "GET /api/session": () =>
      jsonResponse(200, finished ? unlocked : lockedLoggedOut),
    "GET /api/queue?status=pending": () => jsonResponse(200, []),
    "GET /api/grid": () => jsonResponse(200, { columns: [], rows: [] }),
  });
  renderWithQuery(<App />);
  await startLogin();

  cli.print("You are logged in!");
  cli.exit(0);

  expect(
    await waitForPolls(() => screen.getByRole("button", { name: "Log out" })),
  ).toBeInTheDocument();
  const [finish] = callsTo(fetchMock, "POST", "/api/bw-login/finish");
  expect(finish && loginHeader(finish)).toBe(LOGIN_ID);
  expect(screen.queryByLabelText(/Master password/)).not.toBeInTheDocument();
  expectPasswordNotLeaked();
});

test("a failed finish unlocks with the password field like the login page", async () => {
  const { cli, fetchMock } = fakeCli({
    "POST /api/bw-login/finish": () =>
      jsonResponse(500, { detail: "Unlock failed" }),
    "POST /api/session": () =>
      jsonResponse(401, { detail: "Wrong master password" }),
  });
  renderWithQuery(<App />);
  await startLogin();
  cli.exit(0);

  expect(await waitForPolls(() => screen.getByRole("alert"))).toHaveTextContent(
    "Login failed, try again",
  );
  await waitFor(() =>
    expect(screen.getByLabelText(/Master password/)).toHaveFocus(),
  );
  fireEvent.change(screen.getByLabelText(/Master password/), {
    target: { value: PASSWORD },
  });
  fireEvent.click(screen.getByRole("button", { name: "Unlock" }));

  expect(await screen.findByText("Wrong master password")).toBeInTheDocument();
  expect(callsTo(fetchMock, "POST", "/api/session")[0]?.body).toBe(
    JSON.stringify({ password: PASSWORD }),
  );
  expect(callsTo(fetchMock, "POST", "/api/bw-login")).toHaveLength(1);
});

test("a failed exit keeps the output and offers Try again", async () => {
  const { cli } = fakeCli();
  renderWithQuery(<App />);
  await startLogin();

  cli.print("Username or password is incorrect. Try again.");
  cli.exit(1);

  fireEvent.click(
    await waitForPolls(() => screen.getByRole("button", { name: "Try again" })),
  );

  expect(
    screen.getByRole("log", { name: "Bitwarden CLI output" }),
  ).toHaveTextContent("Username or password is incorrect");
  expect(screen.queryByLabelText("Answer to the CLI")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(screen.getByLabelText(/Master password/)).toHaveFocus(),
  );
  expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled();
});

test("Cancel deletes the login and goes back to the form", async () => {
  const { fetchMock } = fakeCli();
  renderWithQuery(<App />);
  await startLogin();

  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

  await waitFor(() =>
    expect(screen.getByLabelText(/Master password/)).toHaveFocus(),
  );
  const [cancel] = callsTo(fetchMock, "DELETE", "/api/bw-login");
  expect(cancel && loginHeader(cancel)).toBe(LOGIN_ID);
  expect(screen.queryByLabelText("Answer to the CLI")).not.toBeInTheDocument();
});

test("a 409 on start shows the reason", async () => {
  fakeCli({
    "POST /api/bw-login": () =>
      jsonResponse(409, { detail: "A login is already running" }),
  });
  renderWithQuery(<App />);
  fireEvent.change(await screen.findByLabelText(/Master password/), {
    target: { value: PASSWORD },
  });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "A login is already running",
  );
  expect(screen.getByLabelText(/Master password/)).toHaveValue("");
  expectPasswordNotLeaked();
});

test("a 422 on start shows the account the worker belongs to", async () => {
  const detail = `This bwtidy worker belongs to ${EMAIL} on ${SERVER}.`;
  fakeCli({ "POST /api/bw-login": () => jsonResponse(422, { detail }) });
  renderWithQuery(<App />);
  fireEvent.change(await screen.findByLabelText(/Master password/), {
    target: { value: PASSWORD },
  });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  expectPasswordNotLeaked();
});

// The gateway with a new account open: no directory and no worker until Log in.
const newAccount = {
  account: { server: SERVER, email: EMAIL },
  running: false,
  exists: false,
  pending: false,
  loggedIn: false,
};

test("without a running worker Log in shows Starting until the login runs", async () => {
  let answer: (response: Response) => void = () => {};
  const started = new Promise<Response>((resolve) => {
    answer = resolve;
  });
  const { fetchMock } = fakeCli({
    "GET /gateway/account": () => jsonResponse(200, newAccount),
    "POST /api/bw-login": (() => started) as unknown as Route,
  });
  renderWithQuery(<App />);
  // the gateway's answer shows the Bitwarden login, no worker is asked
  fireEvent.change(await screen.findByLabelText(/Master password/), {
    target: { value: PASSWORD },
  });
  expect(screen.getByLabelText(/Server/)).toHaveAttribute("readonly");
  expect(callsTo(fetchMock, "GET", "/api/session")).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));

  expect(await screen.findByText("Starting")).toBeInTheDocument();
  answer(jsonResponse(200, { loginId: LOGIN_ID }));
  expect(await screen.findByLabelText("Answer to the CLI")).toBeInTheDocument();
  expect(screen.queryByText("Starting")).not.toBeInTheDocument();
});

test.each([
  [
    429,
    "Too many new accounts wait for their first login. Try again in a few minutes.",
  ],
  [503, "The worker could not start."],
])("a failed start (%i) shows the gateway's text", async (status, detail) => {
  fakeCli({
    "GET /gateway/account": () => jsonResponse(200, newAccount),
    "POST /api/bw-login": () => jsonResponse(status, { detail }),
  });
  renderWithQuery(<App />);
  fireEvent.change(await screen.findByLabelText(/Master password/), {
    target: { value: PASSWORD },
  });
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));

  expect(await screen.findByRole("alert")).toHaveTextContent(detail);
  expect(screen.queryByText("Starting")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Log in" })).toBeEnabled();
  expectPasswordNotLeaked();
});

test("the show button shows and hides the password, and it is hidden after submit", async () => {
  fakeCli({
    "POST /api/bw-login": () =>
      jsonResponse(409, { detail: "A login is already running" }),
  });
  renderWithQuery(<App />);
  const field = await screen.findByLabelText(/Master password/);
  expect(field).toHaveAttribute("type", "password");
  expect(field).toHaveAttribute("name", "password");
  expect(field).toHaveAttribute("autocomplete", "current-password");
  fireEvent.change(field, { target: { value: PASSWORD } });

  fireEvent.click(screen.getByRole("button", { name: "Show master password" }));
  expect(field).toHaveAttribute("type", "text");
  expect((field as HTMLInputElement).value === PASSWORD).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Hide master password" }));
  expect(field).toHaveAttribute("type", "password");

  fireEvent.click(screen.getByRole("button", { name: "Show master password" }));
  fireEvent.click(screen.getByRole("button", { name: "Log in" }));
  await screen.findByRole("alert");
  expect(screen.getByLabelText(/Master password/)).toHaveAttribute(
    "type",
    "password",
  );
  expect(
    screen.getByRole("button", { name: "Show master password" }),
  ).toBeInTheDocument();
});
