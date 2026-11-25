# Run bwtidy

bwtidy runs as one Docker container with the official Bitwarden CLI inside.
You need Docker with Compose. The normal way is the ready-made image on Docker
Hub, `docker.io/tnek213/bwtidy`: `compose.yaml` pulls it, so there is nothing
to build. Its tags:

- `latest` and the versions (like `0.1.0-alpha.1`) are releases. What each
  one brings is in the [Release notes](../releases.md).
- `main` and the short commits follow every push to main that changes the
  image.

GitHub Actions builds every image from this repository and signs a
provenance attestation for it, which shows the commit it was built from.
How to check it is in
[SECURITY.md, Verifying the image](https://github.com/tnek213/bwtidy/blob/main/SECURITY.md#verifying-the-image).
To build the image yourself instead, see
[Build the image yourself](#build-the-image-yourself).

## Start it

1. Get `compose.yaml` and `example.env`, either by cloning the repository or by
   downloading the two files from https://raw.githubusercontent.com/tnek213/bwtidy/main/,
   and make your settings file:

    ```sh
    git clone https://github.com/tnek213/bwtidy.git bwtidy && cd bwtidy
    cp example.env .env
    ```

    Every setting is a variable in `.env`, and `example.env` says what each
    one does. The defaults are fine to start with. All of them are listed in
    [Configuration](../reference/configuration.md).

2. Optionally pin `BWTIDY_VERSION` in `.env` to a version, then start the
   container:

    ```sh
    docker compose up -d
    ```

3. Open http://127.0.0.1:8765. Only that port is published, on the host's
   127.0.0.1. These docs are at http://127.0.0.1:8765/docs/, for anyone who
   can reach that address, without a login.

## Log in the first time

1. Enter the server (https://vault.bitwarden.com unless you use another one)
   and your email, then Continue. An account bwtidy does not know yet is
   offered to be created. It is kept once its Bitwarden login succeeds.

    ![The account form with the server and email filled in and Continue outlined](../images/tutorial-account-form.png)

2. bwtidy shows a Bitwarden login with the server and email filled in. Type
   your master password and click Log in. A console below the form shows
   what the Bitwarden CLI prints.
3. A first login from a new device makes bitwarden.com email you a one-time
   code. Type it into the field under the console and press Enter.
4. bwtidy then unlocks the vault with the same master password, so you type
   it once.

After that the page only asks for the master password to unlock. It shows
"Starting" for a few seconds while the account's worker starts. The browser
remembers the account, and one browser profile works with one account at a
time. Several accounts can use the same URL, each kept apart from the others
(see [How accounts are kept apart](../explanation/isolation.md)).

![The unlock form with the master password typed and Unlock outlined](../images/tutorial-unlock-form.png)

When the session ends, and what happens to your unsaved changes then, is in
[Logging in](../explanation/login.md#the-session).

Do not run `bw unlock`, `bw lock` or `bw logout` in the container while you
are logged in to the web UI. Each of them invalidates the session key bwtidy
uses for backups (see
[How bwtidy uses the Bitwarden CLI](../explanation/bw-cli.md)).

## Build the image yourself

To run unreleased code, or to run only what you built, build the image from a
checkout instead of pulling it. `compose.build.yaml` adds the build to
`compose.yaml`: it builds the `Dockerfile`'s target `app` from the checkout
and names the image `bwtidy:local`, everything else comes from `compose.yaml`
and `.env`. You need buildx 0.17 or later:

```sh
git clone https://github.com/tnek213/bwtidy.git bwtidy && cd bwtidy
cp example.env .env
BWTIDY_COMMIT="$(git log -1 --format='%cd %h' --date=format:%y%m%d)" \
  docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

The version comes from `backend/pyproject.toml` in the checkout. The build
has no `.git`, so the commit comes in as the build arg `BWTIDY_COMMIT`: the
committer date (YYMMDD), a space and the short hash, the way the project's
own image workflow sets it. The About page then shows both, like
`0.1.0-alpha.1 (261111 7db8830)`. Without it, it shows the version alone. The
published images also carry the OCI labels `version` and `revision`
(`org.opencontainers.image.*`), which the workflow sets and a build of your
own leaves out.

With a buildx older than 0.17, build with plain Docker first and leave
`--build` out:

```sh
docker build --target app -t bwtidy:local \
  --build-arg BWTIDY_COMMIT="$(git log -1 --format='%cd %h' --date=format:%y%m%d)" .
docker compose -f compose.yaml -f compose.build.yaml up -d
```

To update, `git pull` and run the same commands again.

## Behind a reverse proxy

Let the proxy (Caddy, nginx, Traefik) on the same host terminate TLS and
forward to `127.0.0.1:8765`. Then set these in `.env`, with your own host
name, and run `docker compose up -d` again (what each one does is in
[Configuration](../reference/configuration.md#set-in-env)):

```sh
BWTIDY_SECURE_COOKIES=1
BWTIDY_MCP_HOSTS=bwtidy.example
```

## Update, stop and logs

- Update: change `BWTIDY_VERSION` in `.env` (or keep `latest`), then run
  `docker compose pull && docker compose up -d`. Your data is kept.
- Stop: `docker compose down`. The volumes, and so your accounts and their
  backups, stay. `docker compose down -v` deletes them.
- Logs: `docker logs bwtidy` for the gateway. Each account's worker writes
  `worker.log` in its account directory.

The About page (More actions, About bwtidy) shows the version and the commit
the running image was built from.
