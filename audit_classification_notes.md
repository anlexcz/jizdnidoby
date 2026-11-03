# Audit klasifikace variant – 2026-11-03

Automatický audit nad `data/history/classifications/2026-11-03.csv.gz` odhalil dvě slabiny klasifikátoru:

1. Persistující regulérní varianty s nízkým relativním podílem na velmi vytížené lince zůstávají `unknown` (typicky linky 7, 17 a 22).
2. Heuristika skrytých `depot_out/depot_in` je příliš agresivní, protože relativní limit 20 % dovolí označit jako výjezd/zátah i variantu s desítkami spojů za den (např. 17 Vozovna Kobylisy → Libuš).

Další úprava klasifikátoru má:
- posílit absolutní četnost a stabilitu regulérních variant,
- zpřísnit depot heuristiku,
- využít `block_id` jako silný provozní důkaz tam, kde je k dispozici,
- preferovat `unknown` před slabě podloženým depot zařazením.
