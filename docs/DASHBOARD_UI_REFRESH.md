# Dashboard organization and usability refresh

This change updates the existing HTML/CSS/JavaScript workspace. No backend route,
market-data adapter, calculation, strategy, signal threshold, stored record or
real/simulated order behavior was changed in this refresh.

## Information structure

| Group | Existing views and tasks |
| --- | --- |
| Markets | Overview, scanner, personal watchlist, global company search |
| Simulation & results | Company Results & Reaction, paper positions, performance report, reaction lab |
| Research | Quant research catalogue, formulas/methodology |
| Workspace | Owner settings, account, display preferences |

The overview leads with session selection/status, concise breadth and the selected
stock chart beside ranked signals. Daily formula rankings follow. The trajectory
explorer stays available in a disclosure, followed by sector context. Exchange
clock, source details, Laya briefing and margin-reference status are grouped under
Market status & data. Existing Flow/Classic routes remain under Other views.

Scanner filters now have persistent labels, a visible result count and a link to
the existing full-catalogue search. Paper positions have symbol/direction search,
state filtering and display sorting without reordering the source ledger. Rules,
journals and hypothetical hindsight are expandable; paper disclosures retain their
state across refreshes. Existing exports and session controls are preserved.

Results retain their existing route. The filing ledger and comparison detail share
a consistent reading hierarchy; a bounded, keyboard-scrollable list prevents the
selected company from being buried on small screens. Source coverage, daily
evidence and news context are disclosures. Research cards summarize implementation
state and expand into formulas, exact rules and limitations.

## Design and accessibility

- Shared muted navy surfaces, blue/red semantics, readable metadata and spacing.
- Labeled navigation at desktop/tablet sizes; mobile drawer with backdrop, close
  control, Escape dismissal, synchronized expanded state and keyboard boundary.
- Active navigation and chart toggles expose state to assistive technology.
- Historical chart drawer has dialog semantics, keyboard containment and focus
  return, preserving the existing resize observer and chart interactions.
- Table overflow is local to keyboard-focusable regions instead of the whole page.
- The repeated historical drawer CSS block was verified identical before removing
  one copy. Hidden spotlight rendering and alternate views were retained.

## Main files

- `marketscope/static/workspace.html`, `workspace.css`, `workspace.js`: shell,
  navigation, overview hierarchy, scanner labels and responsive design.
- `marketscope/static/historical-chart.js`: drawer keyboard accessibility only.
- `marketscope/static/paper-trading.css`, `paper-trading.js`, `paper-watchlist.js`:
  paper tables, controls, disclosures and view-only filters.
- `marketscope/static/results.html`, `results.css`, `results.js`: existing results
  route organization and accessible responsive ledger.
- `marketscope/static/quant-research.js`: expandable evidence catalogue.
- Frontend regression tests and `package.json`: results and historical accessibility
  checks included in the normal frontend command.

## Verification and remaining limits

Run `npm test`. Coverage includes session/timeframe changes, source failures,
chart history and controls, navigation, scanner/watchlist interactions, paper
controls and missing-data behavior, result rendering, research filtering and
drawer focus handling. Node syntax checks passed for all edited JavaScript files;
CSS braces are balanced and only one historical drawer definition remains.

Safari smoke checks covered overview with stored candles, research, saved paper
report, Results & Reaction, navigation expansion/Escape and layouts at different
page scales. The in-app browser preview connection became unavailable, so exact
automated viewport geometry and pixel-by-pixel cross-browser certification were
not performed. Responsive breakpoints were also reviewed in CSS and DOM tests.

Existing data issues remain visible. Results currently reports a collection error
involving `StockDatabaseManager.get_company_symbols`; some financial comparisons
are unavailable, and margin-reference data can be stale. These are backend/data
issues, deliberately not repaired or concealed in this front-end-only task.
Existing alternative terminal routes have not been redesigned. Large financial
tables intentionally scroll horizontally within their labeled regions on phones.
