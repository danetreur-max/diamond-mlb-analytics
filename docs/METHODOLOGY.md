# Methods and limitations

## Scope before comparison

Full-player WAR includes all clubs. Club WAR sums matched source team stints. Source-reported club charges are not independently verified cash paid and may include allocations. Full-season salaries, AAV, tax payroll and future guarantees are different fields. Cost/WAR is undefined for nonpositive WAR and withheld for unsupported salary bases.

## Historical benchmark

The available prior three seasons receive weights 5, 3 and 1. Missing seasons are omitted rather than zero-filled. The benchmark has no age, injury or role adjustment and is not Marcel, ZiPS, Steamer or a machine-learning model. Linear game-pace comparisons are workload-sensitive research leads, not rest-of-season forecasts.

Rolling target-year evaluations exclude all target-year inputs and only use completed calendar years. Players require observed prior history and a target-year MLB WAR record: players who leave MLB are omitted, producing survivorship bias. Downloaded history can reflect retrospective revisions. MAE, RMSE, signed bias and within-one-WAR fraction describe this cohort only; they are not individual confidence intervals or independent validation of archived forecasts.

## Comparable peers

Peers match current directory position group, contract status, WAR provider, age within three years and workload between half and twice the target workload. Salary must have a confirmed full-season basis; ambiguous duplicates are excluded. At least five peers are required. WAR/600 PA or WAR/180 IP normalizes workload, not role, park or context. Median peer salary is descriptive, never a recommended price.

## Availability, contracts and dates

Official team transaction feeds provide reported IL/activation boundaries; missing and retroactive boundaries remain incomplete. Calendar intervals do not prove games missed or medical cause. Historical contract rows are stored annual snapshots, not contemporaneous signed contract schedules. Unverified retained salary, options, deferrals and buyouts are not invented.

Actual bWAR and public Steamer fWAR forecasts are kept separate. An unverified original publication date is shown as a quality flag. Publicly readable snapshots are used; paid archives are not bypassed. Every source can be revised. The static demo is explicitly frozen and cannot establish current standings, contracts or roster status.

## Sources

- [MLB Stats API](https://statsapi.mlb.com/)
- [MLB source notice](https://gdx.mlb.com/components/copyright.txt)
- [FanGraphs RosterResource](https://www.fangraphs.com/roster-resource/payroll/braves)
- [Baseball Reference WAR explanation](https://www.baseball-reference.com/about/war_explained.shtml)
- [Public FanGraphs projections](https://www.fangraphs.com/projections)

Source availability is not a grant of commercial redistribution rights. MIT applies to original project code only.
