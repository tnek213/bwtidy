# uPlot

The chart of the demo's start page (dev/demo.md, Usage stats). The two files
are copied unchanged from the npm package, which the demo gateway serves under
`/demo/static/uplot/`, so the page loads nothing from another host.

- Package: uplot
- Version: 1.6.32
- Source: <https://registry.npmjs.org/uplot/-/uplot-1.6.32.tgz>
- Tarball sha512 (npm's integrity): `sha512-KIMVnG68zvu5XXUbC4LQEPnhwOxBuLyW1AHtpm6IKTXImkbLgkMy+jabjLgSLMasNuGGzQm/ep3tOkyTxpiQIw==`
- License: MIT, the text is `package/LICENSE` in the tarball

The files, from `package/dist/` in the tarball:

| File                | sha512                                                                                                                             |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `uPlot.iife.min.js` | `8df9b8c2bc60abbdd95c5cf260f46681f41def2623b6c1f9ed2cdf823a62797e2b8277c700c01926043560b4ec73a1ce5fed6d9c68e7905b9eadb73ca08da626` |
| `uPlot.min.css`     | `c57618bc6a0fa8e8e13309299315930efec7be5cab93212acdce14485aff66d0580bb7a4944dc33410dc72a88c8f657344a49f7d9cdea9545c91d633271f6a7d` |

To update: take the newest 1.6.x release on npm that is at least two weeks
old, check its tarball against the integrity npm lists for it, copy the two
files and write the new version and sums here. `demo/tests/test_stats.py`
checks the files against this table.
