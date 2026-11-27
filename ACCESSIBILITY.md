# Accessibility statement

bwtidy's web interface is meant to be usable with a keyboard and a screen
reader. bwtidy is beta software with one maintainer, so this statement says
what has been checked, what has not and what is known to fall short.

## Commitment

An accessibility barrier is a bug. It is reported, fixed and tested like any
other bug, and a fix comes with a test where the test tools can show it.
There is no promise of a response time (see [CONTRIBUTING.md](CONTRIBUTING.md)).

## Not audited

No accessibility audit has been done, and no person has used bwtidy with a
screen reader. The UI is built with MUI and AG Grid, which bring keyboard and
screen reader support of their own, and the web UI was reviewed in November
2026 with these tools:

- axe-core (WCAG 2.2 A and AA rules and axe's best practices) on every page,
  menu and dialog, in the light and the dark color mode.
- The browser's accessibility tree, checked page by page.
- Browser tests that press only keys: log in, find, edit, reveal and hide a
  value, the header and cell menus, every app bar menu and dialog, approve and
  reject on the Queue, restore from the Change log.

These tests run in the project's checks. They find many barriers but not all,
and no virtual or real screen reader was run. Treat everything below as the
result of those checks, not as a claim of conformance to WCAG or any other
standard.

## What works

- Everything can be done with the keyboard. Tab and Shift+Tab move between
  controls, every control shows a focus ring and Escape closes a menu or a
  dialog and returns the focus to what opened it.
- The grid is one stop for Tab. The arrow keys move between its cells and
  headers and Tab leaves it. The context menu key or Shift+F10 opens the cell
  menu and the header menu, which also hold the actions that are otherwise
  small buttons in a cell (such as Reveal value). The keys are listed in the
  [Keyboard section of the UI reference](docs/reference/ui.md#keyboard).
- A cell that carries a mark shown in color or in a tooltip (a queued change,
  a field to fill in, a failed save) gives it as a description once it has the
  focus. A masked value reads as Hidden, and a revealed one is read out.
- The pages have landmarks and a heading, tables have named headers and the
  Queue names its pending count.
- A write that starts or ends and new proposals from an agent are announced.
  Failures come as alerts.
- Text and controls pass axe's contrast rules (4.5:1 for text) in the light
  and the dark color mode, except for the one case listed below.
- A color mode menu offers System, Light and Dark.

## Known limitations

- A cell in edit mode is announced as "Input Editor", without the column it
  belongs to.
- A column header says its short name (such as username), not its group (such
  as Login) as AG Grid shows the group in a header row above.
- The build commit after the title in the app bar is faint on purpose (1.8:1).
  The About page shows it in full contrast.
- MUI's hover tint on a text button in the primary blue gives 4.4:1 in the
  light mode, just under 4.5:1.
- After Commit is pressed the button goes away, and the keyboard focus falls
  back to the start of the page.
- The explanation of an operation in the Change log is a hover tooltip on a
  cell. A screen reader gets it as the cell's description, but Tab does not
  reach it.
- Menus, popovers and tooltips sit outside the page's landmarks, as MUI places
  them.

## Browsers

Neither MUI nor AG Grid, nor bwtidy, names a list of supported browsers or
screen readers for accessibility. The browser tests run in Chromium. bwtidy
has not been checked with any screen reader.

## Report a barrier

[Open an issue with the Bug report form](https://github.com/tnek213/bwtidy/issues/new?template=bug_report.yml).
Say what you wanted to do, what happened, and the browser and assistive
technology you use. Never paste vault contents, secrets or backups into an
issue.
