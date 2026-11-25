// What imports what under src/ (docs/architecture.md, Frontend). `npm run lint:deps` checks it.
const PAGES = '(about|appbar|backups|changelog|grid|queue|session|settings|statistics|templates)'
// The rules of the layers hold for the production code. A test and the test helpers may import
// what they test from anywhere, while nothing in production imports them.
const TESTS = ['\\.test\\.tsx?$', '^src/test-utils\\.tsx$', '^src/test-setup\\.ts$', '/testSupport\\.tsx?$']

module.exports = {
  forbidden: [
    { name: 'no-circular', severity: 'error', from: {}, to: { circular: true } },
    // $1 is the page directory that from.path captures, so a page may import itself
    {
      name: 'pages-do-not-import-each-other',
      severity: 'error',
      from: { path: `^src/${PAGES}/`, pathNot: TESTS },
      to: { path: `^src/${PAGES}/`, pathNot: '^src/$1/' },
    },
    {
      name: 'api-imports-nothing-above',
      severity: 'error',
      from: { path: '^src/api/', pathNot: TESTS },
      to: { path: '^src/(?!api/)' },
    },
    {
      name: 'shared-imports-api-only',
      severity: 'error',
      from: { path: '^src/shared/', pathNot: TESTS },
      to: { path: '^src/(?!api/|shared/)' },
    },
    {
      name: 'commit-imports-api-and-shared-only',
      severity: 'error',
      from: { path: '^src/commit/', pathNot: TESTS },
      to: { path: '^src/(?!api/|shared/|commit/)' },
    },
    {
      name: 'theme-is-a-leaf',
      severity: 'error',
      from: { path: '^src/theme/', pathNot: TESTS },
      to: { path: '^src/(?!theme/)' },
    },
    {
      name: 'only-app-imports-pages',
      severity: 'error',
      from: { pathNot: ['^src/App\\.tsx$', '^src/main\\.tsx$', ...TESTS] },
      to: { path: '^src/[a-z]+/[A-Z][A-Za-z]+Page\\.tsx$' },
    },
    {
      name: 'no-test-imports-in-production',
      severity: 'error',
      from: { pathNot: TESTS },
      to: { path: TESTS },
    },
  ],
  options: {
    tsConfig: { fileName: 'tsconfig.app.json' },
    // type-only imports count, so a cycle through types fails too
    tsPreCompilationDeps: true,
    doNotFollow: { path: 'node_modules' },
  },
}
