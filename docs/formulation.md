# Formulation

This page states the mathematics of the framework. Equation numbers are those of the article's Methods:

- (1)–(46): the hourly operation and sizing model (a mixed-integer linear program);
- (47)–(55): the lifecycle techno-economic analysis (TEA);
- (56): the generator of ancillary-service capacity prices used for synthetic data.

Symbols follow the article's Supplementary Tables 1–3. Where the article leaves a detail open ("the formulation varies by market", "similar constraints exist for all services on all components"), the choice made here is listed in [Conventions of this implementation](#conventions-of-this-implementation). Names in `code font` are the fields, variables and columns of the package.

## Contents

1. [System](#system)
2. [Sets and indices](#sets-and-indices)
3. [Parameters](#parameters)
4. [Decision variables](#decision-variables)
5. [Objective and revenues, Eqs. (1)–(6)](#objective-and-revenues)
6. [System balances, Eqs. (7)–(9)](#system-balances)
7. [Turbine, Eqs. (10)–(16)](#turbine)
8. [Electrolyzer, Eqs. (17)–(29)](#electrolyzer)
9. [Battery, Eqs. (30)–(35)](#battery)
10. [Hydrogen storage, Eqs. (36)–(40)](#hydrogen-storage)
11. [Ancillary services, Eqs. (41)–(46)](#ancillary-services)
12. [Conventions of this implementation](#conventions-of-this-implementation)
13. [Techno-economic analysis, Eqs. (47)–(55)](#techno-economic-analysis)
14. [Synthetic market data, Eq. (56)](#synthetic-market-data)
15. [Flexibility indicators](#flexibility-indicators)
16. [Scope stated in the article](#scope-stated-in-the-article)

## System

The integrated energy system (IES) consists of:

- a nuclear reactor that delivers a constant thermal power $`Q^{steam,total}`$;
- a steam turbine;
- a water electrolyzer: high-temperature electrolysis (HTE), which takes both electricity and steam (a low-temperature electrolyzer is the special case with zero heat use);
- a hydrogen store;
- a battery energy storage system (BESS);
- a grid connection for buying and selling electricity;
- participation in the market's ancillary services (AS).

The electrolyzer power capacity, the battery energy capacity and the hydrogen storage capacity are either fixed parameters or sizing variables (Supplementary Tables 2–3: "these may be treated as either variables or parameters").

Ancillary-service participation has three modes (`Market.as_mode`):

| mode | meaning |
|---|---|
| `off` | no ancillary services |
| `bid` | capacity is offered and paid (Eq. 4); deployment is not executed in the dispatch |
| `dispatch` | the "dispatch execution mode" of Eqs. (45)–(46): the expected deployment moves each component's actual power away from its set point |

## Sets and indices

| Symbol | Meaning | In code |
|---|---|---|
| $`t \in T`$ | time periods of the horizon | dimension `t`; `TimeSeries.hours` |
| $`k \in K_{turb}`$ | breakpoints of the turbine curve | `Turbine.steam_breakpoints_mwth` |
| $`j \in K_{elec}`$ | breakpoints of the electrolyzer curve | `Electrolyzer.power_breakpoints_mw` |
| $`c \in C`$ | components that can provide AS: turbine, electrolyzer, battery | `Service.components` |
| $`s \in S`$ | modeled ancillary services; $`S_{up}`$, $`S_{down}`$ upward and downward ones | `Market.services`, `Service.direction` |

## Parameters

Money in US\$, power in MW, steam in MW_th, energy in MWh, hydrogen in kg.

| Symbol | Meaning | Unit | Field |
|---|---|---|---|
| $`\Delta t`$ | duration of a period | h | `TimeSeries.dt_h` |
| $`\lambda^{E}_t`$ | electricity price | \$/MWh | `TimeSeries.energy_price` |
| $`\lambda^{H_2}`$ | hydrogen price | \$/kg | `Market.h2_price_usd_per_kg` |
| $`\lambda^{H_2,sub}`$ | hydrogen production credit in the operation model | \$/kg | `Market.h2_credit_usd_per_kg` |
| $`\lambda^{AS,cap}_{t,s}`$ | AS capacity clearing price | \$/MW per h | `TimeSeries.as_price` |
| $`w_{t,s}`$ | winning rate of a bid | – | `TimeSeries.winning_rate` |
| $`\delta_{t,s}`$ | deployment factor | – | `TimeSeries.deployment` |
| $`M_{t,s}`$, $`P_{t,s}`$ | regulation mileage and performance factors | – | `TimeSeries.mileage`, `TimeSeries.performance` |
| $`\tau^{AS}`$ | deployment duration for the energy check | h | `Market.reserve_duration_h` |
| $`Q^{steam,total}`$ | reactor thermal power | MW_th | `Turbine.steam_total_mwth` |
| $`P^{turb}_{max}`$, $`P^{turb}_{min}`$ | turbine power limits | MW | `Turbine.p_max_mw`, `p_min_mw` |
| $`Q^{turb,bp}_k`$, $`P^{turb,bp}_k`$ | turbine curve: steam and power at breakpoints | MW_th, MW | `Turbine.steam_breakpoints_mwth`, `power_breakpoints_mw` |
| $`R^{turb,up}_{max}`$, $`R^{turb,down}_{max}`$ | turbine ramp limits | MW/h | `Turbine.ramp_up_mw_per_h`, `ramp_down_mw_per_h` |
| $`C^{turb,vom}`$ | turbine variable O&M | \$/MWh | `Turbine.vom_usd_per_mwh` |
| $`P^{elec}_{max}`$ | electrolyzer capacity (parameter or variable) | MW | `Electrolyzer.capacity_mw`, or sized within `capacity_range_mw` |
| $`P^{elec,on}_{min}`$ | minimum power when on | MW | `Electrolyzer.min_on_mw` |
| $`P^{elec,bp}_j`$ | power breakpoints | MW | `Electrolyzer.power_breakpoints_mw` |
| $`\dot{M}^{H_2,bp}_j`$ | production at breakpoints, $`1000\,P^{elec,bp}_j / k_{e,j}`$ (MW and kWh/kg) | kg/h | from `electricity_kwh_per_kg` |
| $`k_{t,j}`$ | heat use per kg (HTE) | kWh_th/kg | `Electrolyzer.heat_kwh_per_kg` |
| $`R^{elec,up}_{max}`$, $`R^{elec,down}_{max}`$ | ramp limits, fraction of capacity per hour | 1/h | `ramp_up_fraction_per_h`, `ramp_down_fraction_per_h` |
| $`U^{elec}_{init}`$ | initial on/off status, with the hours already spent in it | –, h | `Electrolyzer.initial_state_h` (> 0 on, < 0 off) |
| $`T^{elec,up}_{min}`$, $`T^{elec,down}_{min}`$ | minimum up and down times | h | `min_up_h`, `min_down_h` |
| $`C^{elec,vom}`$, $`C^{elec,su}`$, $`C^{H_2O}`$ | variable O&M, startup cost, water cost | \$/MWh, \$, \$/kg | `vom_usd_per_mwh`, `startup_cost_usd`, `water_cost_usd_per_kg` |
| $`C^{elec,capex}`$ | annualized electrolyzer capital cost | \$/MW-yr | `Electrolyzer.capex_usd_per_mw_yr` |
| $`D_{op}`$, $`D_{su}`$ | degradation per full-load hour and per start | – | `degradation_per_h_at_full_load`, `degradation_per_start` |
| $`CF^{H_2}_{target}`$ | target minimum capacity factor (optional) | – | `Electrolyzer.target_capacity_factor` |
| $`E^{b}_{max}`$ | battery energy capacity (parameter or variable) | MWh | `Battery.energy_mwh`, or sized within `energy_range_mwh` |
| $`\mathrm{PowerRatio}`$ | battery power per energy | MW/MWh | `Battery.power_per_energy` |
| $`\eta^{b,ch}`$, $`\eta^{b,dis}`$ | charge and discharge efficiencies | – | `charge_efficiency`, `discharge_efficiency` |
| $`E^{b}_{init}`$, $`\mathrm{SOC}_{min}`$ | initial state of charge, minimum state of charge (fractions of capacity in code) | MWh, – | `soc_initial_fraction`, `soc_min_fraction` |
| $`R^{b}_{max}`$ | battery ramp limit (optional) | 1/h | `Battery.ramp_fraction_per_h` |
| $`C^{b,vom}`$, $`C^{b,capex,E}`$, $`C^{b,capex,P}`$ | battery variable O&M, annualized energy and power capital cost | \$/MWh, \$/MWh-yr, \$/MW-yr | `vom_usd_per_mwh`, `capex_energy_usd_per_mwh_yr`, `capex_power_usd_per_mw_yr` |
| $`M^{stor}_{max}`$, $`M^{stor}_{min}`$ | storage capacity (parameter or variable) and inventory floor | kg | `HydrogenStorage.capacity_kg`, or sized within `capacity_range_kg`; `inventory_min_kg` |
| $`\dot{M}^{stor,ch}_{max}`$, $`\dot{M}^{stor,dis}_{max}`$ | charge and discharge rate limits | kg/h | `charge_max_kg_per_h`, `discharge_max_kg_per_h` |
| $`\eta^{stor,ch}`$, $`\eta^{stor,dis}`$, $`M^{stor}_{init}`$ | storage efficiencies and initial inventory | –, kg | `charge_efficiency`, `discharge_efficiency`, `initial_kg` |
| $`C^{stor,vom}`$, $`C^{stor,capex}`$ | storage cycling cost, annualized capital cost | \$/kg, \$/kg-yr | `vom_usd_per_kg`, `capex_usd_per_kg_yr` |

## Decision variables

Hourly columns of `Operation.hourly` are given in brackets.

| Symbol | Meaning | Unit |
|---|---|---|
| $`p^{IES}_t`$ | net exchange with the grid, positive for sale [`p_ies`] | MW |
| $`p^{sale}_t`$, $`p^{buy}_t`$ | power sold and bought [`p_sale`, `p_buy`] | MW |
| $`p^{turb}_t`$, $`q^{turb}_t`$ | turbine power and steam [`p_turb`, `q_turb`] | MW, MW_th |
| $`p^{elec}_t`$, $`p^{elec,sp}_t`$ | actual and set-point electrolyzer power [`p_elec`, `p_elec_setpoint`] | MW |
| $`\dot{m}^{H_2}_t`$, $`q^{elec}_t`$ | hydrogen production and electrolyzer steam [`h2_production`, `q_elec`] | kg/h, MW_th |
| $`d_t`$ | electrolyzer degradation state [`degradation`] | – |
| $`p^{b,ch}_t`$, $`p^{b,dis}_t`$, $`e^{b}_t`$ | battery charge, discharge, state of charge [`p_charge`, `p_discharge`, `soc`] | MW, MWh |
| $`m^{stor}_t`$ | hydrogen inventory [`h2_inventory`] | kg |
| $`\dot{m}^{stor,in}_t`$, $`\dot{m}^{stor,out}_t`$, $`\dot{m}^{H_2,market}_t`$ | flows into and out of the store, direct sales [`h2_to_storage`, `h2_from_storage`, `h2_to_market`] | kg/h |
| $`\dot{m}^{H_2,sale}_{const}`$ | constant sales rate when the store is sized | kg/h |
| $`b^{c}_{t,s}`$, $`d^{c}_{t,s}`$ | AS bid and deployed capacity of component $`c`$ [`bid_<c>_<s>`, `dep_<c>_<s>`] | MW |
| $`\lambda_{t,k}`$, $`\lambda_{t,j}`$ | SOS2 weights of the piecewise-linear curves (incremental form here) | – |
| $`P^{elec}_{max}`$, $`E^{b}_{max}`$, $`P^{b}_{max}`$, $`M^{stor}_{max}`$ | sizing variables [`Operation.capacities`] | MW, MWh, MW, kg |
| $`u^{elec}_t`$, $`v^{elec}_t`$, $`w^{elec}_t`$ | electrolyzer on/off, startup, shutdown (binary) [`elec_on`, `elec_start`, `elec_stop`] | – |
| $`u^{b,ch}_t`$, $`u^{b,dis}_t`$ | battery charging, discharging status (binary) | – |

## Objective and revenues

The model maximizes the profit over the horizon:

```math
\Pi=\left(R^{E}+R^{H_2}+R^{AS}\right)-\left(C^{OPEX}+C^{CAPEX}\right) \qquad (1)
```

Electricity sales minus purchases:

```math
R^{E}=\sum_{t\in T}\Delta t\,\lambda^{E}_{t}\left(p^{sale}_{t}-p^{buy}_{t}\right) \qquad (2)
```

Hydrogen sales (from the store and directly) and the production credit (on all production):

```math
R^{H_2}=\sum_{t\in T}\Delta t\left(\left(\dot{m}^{stor,out}_{t}+\dot{m}^{H_2,market}_{t}\right)\lambda^{H_2}+\dot{m}^{H_2}_{t}\,\lambda^{H_2,sub}\right) \qquad (3)
```

Ancillary-service revenue of a service $`s`$, with $`b_{t,s}`$ the total bid of Eq. (43): a capacity payment and an energy payment on deployment.

```math
R^{AS}_{s}=\sum_{t\in T}\Delta t\left(b_{t,s}\,w_{t,s}\,\lambda^{AS,cap}_{t,s}+\mathrm{EnergyPayment}_{s}\right),\qquad \mathrm{EnergyPayment}_{s}=b_{t,s}\,w_{t,s}\,\delta_{t,s}\,\lambda^{E}_{t} \qquad (4)
```

with $`R^{AS}=\sum_{s\in S}R^{AS}_{s}`$. Variable operating cost:

```math
\begin{aligned}
C^{OPEX}={}&\sum_{t\in T}\Delta t\Big(C^{turb,vom}p^{turb}_{t}+C^{elec,vom}p^{elec}_{t}+C^{H_2O}\dot{m}^{H_2}_{t}+C^{b,vom}\frac{p^{b,ch}_{t}+p^{b,dis}_{t}}{2}+C^{stor,vom}\left(\dot{m}^{stor,in}_{t}+\dot{m}^{stor,out}_{t}\right)\Big)\\
&+\sum_{t\in T}C^{elec,su}\,v^{elec}_{t}
\end{aligned} \qquad (5)
```

Annualized capital cost of the sized components, scaled to the horizon:

```math
C^{CAPEX}=\frac{|T|\,\Delta t}{8760}\left(C^{elec,capex}P^{elec}_{max}+C^{b,capex,E}E^{b}_{max}+C^{b,capex,P}P^{b}_{max}+C^{stor,capex}M^{stor}_{max}\right) \qquad (6)
```

The annualized capital costs are, for example, overnight costs times the capital recovery factor of Eq. (55) (`nuclear_h2.annualized_capex`).

## System balances

```math
p^{turb}_{t}+p^{b,dis}_{t}-p^{elec}_{t}-p^{b,ch}_{t}=p^{IES}_{t}\quad\forall t\in T \qquad (7)
```

```math
p^{IES}_{t}=p^{sale}_{t}-p^{buy}_{t}\quad\forall t\in T \qquad (8)
```

The reactor's steam goes to the turbine and the electrolyzer:

```math
q^{turb}_{t}+q^{elec}_{t}=Q^{steam,total}\quad\forall t\in T \qquad (9)
```

## Turbine

```math
P^{turb}_{min}\le p^{turb}_{t}\le P^{turb}_{max}\quad\forall t\in T \qquad (10)
```

```math
p^{turb}_{t}-p^{turb}_{t-1}\le R^{turb,up}_{max}\,\Delta t\quad\forall t\in T,\ t>1 \qquad (11)
```

```math
p^{turb}_{t-1}-p^{turb}_{t}\le R^{turb,down}_{max}\,\Delta t\quad\forall t\in T,\ t>1 \qquad (12)
```

Turbine power is a piecewise-linear function of its steam:

```math
q^{turb}_{t}=\sum_{k\in K_{turb}}\lambda_{t,k}\,Q^{turb,bp}_{k}\quad\forall t\in T \qquad (13)
```

```math
p^{turb}_{t}=\sum_{k\in K_{turb}}\lambda_{t,k}\,P^{turb,bp}_{k}\quad\forall t\in T \qquad (14)
```

```math
\sum_{k\in K_{turb}}\lambda_{t,k}=1\quad\forall t\in T \qquad (15)
```

```math
\{\lambda_{t,k}\}_{k\in K_{turb}}\ \text{is an SOS2 set}\quad\forall t\in T \qquad (16)
```

## Electrolyzer

Operating limits, gated by the on/off status when startup and shutdown logic is enabled:

```math
p^{elec}_{t}\ge P^{elec,on}_{min}\,u^{elec}_{t}\quad\forall t\in T \qquad (17)
```

```math
p^{elec}_{t}\le P^{elec}_{max}\,u^{elec}_{t}\quad\forall t\in T \qquad (18)
```

Ramp limits:

```math
p^{elec}_{t}-p^{elec}_{t-1}\le P^{elec}_{max}\,R^{elec,up}_{max}\,\Delta t\quad\forall t\in T,\ t>1 \qquad (19)
```

```math
p^{elec}_{t-1}-p^{elec}_{t}\le P^{elec}_{max}\,R^{elec,down}_{max}\,\Delta t\quad\forall t\in T,\ t>1 \qquad (20)
```

Hydrogen production is a piecewise-linear function of power:

```math
p^{elec}_{t}=\sum_{j\in K_{elec}}\lambda_{t,j}\,P^{elec,bp}_{j}\quad\forall t\in T \qquad (21)
```

```math
\dot{m}^{H_2}_{t}=\sum_{j\in K_{elec}}\lambda_{t,j}\,\dot{M}^{H_2,bp}_{j}\quad\forall t\in T \qquad (22)
```

```math
\sum_{j\in K_{elec}}\lambda_{t,j}=1\quad\forall t\in T \qquad (23)
```

```math
\{\lambda_{t,j}\}_{j\in K_{elec}}\ \text{is an SOS2 set}\quad\forall t\in T \qquad (24)
```

A similar piecewise-linear relation links the HTE steam $`q^{elec}_{t}`$ to its power; here the steam at breakpoint $`j`$ is $`\dot{M}^{H_2,bp}_{j}\,k_{t,j}/1000`$ (MW_th, with $`k_{t,j}`$ in kWh_th/kg), on the same weights.

Startup and shutdown logic with minimum up and down times:

```math
u^{elec}_{t}-u^{elec}_{t-1}=v^{elec}_{t}-w^{elec}_{t}\quad\forall t\in T,\ t>1 \qquad (25)
```

```math
v^{elec}_{t}+w^{elec}_{t}\le 1\quad\forall t\in T \qquad (26)
```

```math
\sum_{i=t-T^{elec,up}_{min}+1}^{t}u^{elec}_{i}\ge T^{elec,up}_{min}\,v^{elec}_{t}\quad\forall t\ge T^{elec,up}_{min} \qquad (27)
```

```math
\sum_{i=t-T^{elec,down}_{min}+1}^{t}\left(1-u^{elec}_{i}\right)\ge T^{elec,down}_{min}\,w^{elec}_{t}\quad\forall t\ge T^{elec,down}_{min} \qquad (28)
```

Cumulative degradation:

```math
d_{t}=d_{t-1}+D_{op}\,\frac{p^{elec}_{t}}{P^{elec}_{max}}\,\Delta t+D_{su}\,v^{elec}_{t}\quad\forall t\in T,\ t>1 \qquad (29)
```

## Battery

```math
e^{b}_{t}=e^{b}_{t-1}+\Delta t\left(\eta^{b,ch}p^{b,ch}_{t}-\frac{p^{b,dis}_{t}}{\eta^{b,dis}}\right)\quad\forall t\in T,\ t>1 \qquad (30)
```

For $`t=1`$, Eq. (30) starts from $`E^{b}_{init}`$.

```math
p^{b,ch}_{t}\le P^{b}_{max}\,u^{b,ch}_{t}\quad\forall t\in T \qquad (31)
```

```math
p^{b,dis}_{t}\le P^{b}_{max}\,u^{b,dis}_{t}\quad\forall t\in T \qquad (32)
```

```math
u^{b,ch}_{t}+u^{b,dis}_{t}\le 1\quad\forall t\in T \qquad (33)
```

```math
E^{b}_{max}\,\mathrm{SOC}_{min}\le e^{b}_{t}\le E^{b}_{max}\quad\forall t\in T \qquad (34)
```

```math
P^{b}_{max}=E^{b}_{max}\cdot\mathrm{PowerRatio}\quad(\text{if sizing}) \qquad (35)
```

## Hydrogen storage

```math
m^{stor}_{t}=m^{stor}_{t-1}+\Delta t\left(\eta^{stor,ch}\dot{m}^{stor,in}_{t}-\frac{\dot{m}^{stor,out}_{t}}{\eta^{stor,dis}}\right)\quad\forall t\in T,\ t>1 \qquad (36)
```

```math
\dot{m}^{H_2}_{t}=\dot{m}^{H_2,market}_{t}+\dot{m}^{stor,in}_{t}\quad\forall t\in T \qquad (37)
```

```math
M^{stor}_{min}\le m^{stor}_{t}\le M^{stor}_{max}\quad\forall t\in T \qquad (38)
```

```math
\dot{m}^{stor,in}_{t}\le\dot{M}^{stor,ch}_{max}\quad\forall t\in T \qquad (39)
```

```math
\dot{m}^{stor,out}_{t}\le\dot{M}^{stor,dis}_{max}\quad\forall t\in T \qquad (40)
```

When the storage capacity is sized, sales leave the store at a constant rate, $`\dot{m}^{stor,out}_{t}=\dot{m}^{H_2,sale}_{const}`$, and all production passes through the store, $`\dot{m}^{H_2,market}_{t}=0`$.

## Ancillary services

Bids are limited by each component's headroom and ramp capability. Upward reserve from the turbine:

```math
p^{turb}_{t}+\sum_{s\in S_{up}}b^{turb}_{t,s}\le P^{turb}_{max}\quad\forall t\in T \qquad (41)
```

Downward reserve from the electrolyzer (a load increase):

```math
p^{elec,sp}_{t}+\sum_{s\in S_{down}}b^{elec}_{t,s}\le P^{elec}_{max}\,u^{elec}_{t}\quad\forall t\in T \qquad (42)
```

The article states that similar constraints hold for all services on all components, including ramp rates and the battery's state of charge; the set used here is listed under [Conventions](#conventions-of-this-implementation), item 6.

Total bid per service:

```math
b_{t,s}=\sum_{c\in C}b^{c}_{t,s}\quad\forall t\in T,\ s\in S \qquad (43)
```

Regulation balance, for markets that require equal up and down regulation:

```math
b^{c}_{t,\mathrm{RegUp}}=b^{c}_{t,\mathrm{RegDown}}\quad\forall t\in T,\ c\in C \qquad (44)
```

In the dispatch execution mode, the deployed amount follows the winning bid:

```math
d^{c}_{t,s}=b^{c}_{t,s}\,w_{t,s}\,\delta_{t,s}\quad\forall t\in T,\ s\in S,\ c\in C \qquad (45)
```

and the actual power of a component moves from its set point by the deployment; for the electrolyzer:

```math
p^{elec}_{t}=p^{elec,sp}_{t}-\sum_{s\in S_{up}}d^{elec}_{t,s}+\sum_{s\in S_{down}}d^{elec}_{t,s}\quad\forall t\in T \qquad (46)
```

## Conventions of this implementation

1. **Piecewise-linear curves.** The SOS2 sets of Eqs. (13)–(16) and (21)–(24) are written in the incremental (delta) form, an exact equivalent that any MILP solver accepts. With breakpoints $`x_0<\dots<x_K`$:

   ```math
   x_t=x_0\,o_t+\sum_{k=1}^{K}\delta_{t,k}\left(x_k-x_{k-1}\right),\qquad 0\le\delta_{t,k}\le 1,\qquad \delta_{t,k+1}\le z_{t,k}\le\delta_{t,k},\qquad \delta_{t,1}\le o_t
   ```

   with $`z_{t,k}`$ binary and the same $`\delta`$ for every curve of the component (power, hydrogen, steam). For the turbine $`o_t=1`$; for the electrolyzer $`o_t=u^{elec}_{t}`$, so all terms vanish when it is off. Collinear interior breakpoints of the turbine curve are merged first; the electrolyzer curves use all their breakpoints.
2. **Sizing variables times binaries.** In Eqs. (18), (31), (32) and (42), the product of a sized capacity and a binary is written exactly with the sizing bound $`\bar{P}`$: $`p\le P_{max}`$ and $`p\le\bar{P}\,u`$.
3. **First hour, minimum up and down times, startup ramps.** Eq. (25) at $`t=1`$ uses $`U^{elec}_{init}`$; Eqs. (30) and (36) start from $`E^{b}_{init}=`$ `soc_initial_fraction` $`\times E^{b}_{max}`$ and $`M^{stor}_{init}`$. This implementation imposes the minimum up and down times of Eqs. (27)–(28) in the startup–shutdown window form: a start within the last $`T^{elec,up}_{min}`$ periods requires the unit to be on, and a stop within the last $`T^{elec,down}_{min}`$ periods requires it to be off,

   ```math
   \sum_{i=t-T^{elec,up}_{min}+1}^{t}v^{elec}_{i}\le u^{elec}_{t},\qquad \sum_{i=t-T^{elec,down}_{min}+1}^{t}w^{elec}_{i}\le 1-u^{elec}_{t}\qquad\forall t\in T
   ```

   with the sums truncated at the start of the horizon and the times converted from hours to periods ($`\lceil T/\Delta t\rceil`$). The hours already spent in the initial state (`initial_state_h`) count toward these times: a unit that has been on for $`h`$ hours stays on for the first $`\min\left(|T|,\ \max\left(0,\ \lceil (T^{elec,up}_{min}-h)/\Delta t\rceil\right)\right)`$ periods, and likewise for a unit that has been off. A start or stop near the end of the horizon is not followed beyond it, and no ramp limit links the first period to the time before the horizon. Between consecutive periods, the ramp limits of Eqs. (19)–(20) also apply to startups and shutdowns, so the ramp rate must allow reaching $`P^{elec,on}_{min}`$ within one period.
4. **End of the horizon.** `Battery.end_soc_fraction`, when given, fixes the final state of charge as a fraction of the energy capacity (equal to `soc_initial_fraction` for a cyclic schedule); with `HydrogenStorage.cyclic` the final inventory is at or above the initial one.
5. **Degradation, Eq. (29),** does not enter the objective or other constraints, so it is evaluated from the solved dispatch (`results.degradation`), starting from zero.
6. **Headroom of every component** (the "similar constraints" after Eq. 42). With $`U_c`$ and $`D_c`$ the sums of a component's upward and downward bids (zero for a direction it does not offer), all of the following are imposed for every component that offers any service, so the set point itself stays within the component's limits:
   - turbine: Eq. (41) on the set point, $`p^{turb,sp}_t-D_{turb}\ge P^{turb}_{min}`$, $`U_{turb}\le R^{turb,up}_{max}\Delta t`$, $`D_{turb}\le R^{turb,down}_{max}\Delta t`$;
   - electrolyzer: Eq. (42) (and $`\le P^{elec}_{max}`$ when sized), $`p^{elec,sp}_t-U_{elec}\ge P^{elec,on}_{min}u^{elec}_t`$, $`U_{elec}\le P^{elec}_{max}R^{elec,down}_{max}\Delta t`$, $`D_{elec}\le P^{elec}_{max}R^{elec,up}_{max}\Delta t`$; an electrolyzer that is off offers nothing;
   - battery, with net output $`g_t=p^{b,dis}_t-p^{b,ch}_t`$: $`g^{sp}_t+U_b\le P^{b}_{max}`$, $`g^{sp}_t-D_b\ge-P^{b}_{max}`$, and enough energy to sustain the bids for $`\tau^{AS}`$ hours: $`e^{b}_t-\mathrm{SOC}_{min}E^{b}_{max}\ge\tau^{AS}U_b/\eta^{b,dis}`$, $`E^{b}_{max}-e^{b}_t\ge\tau^{AS}\eta^{b,ch}D_b`$.

   Each service lists the components that may offer it (`Service.components`).
7. **Set points and modes.** In `bid` mode the set point equals the actual power. In `dispatch` mode, Eq. (46) and its analogues for the turbine ($`p^{turb}_t=p^{turb,sp}_t+\sum_{S_{up}}d^{turb}_{t,s}-\sum_{S_{down}}d^{turb}_{t,s}`$) and the battery's net output define the set points; all physical constraints (Eqs. 9–40) apply to the actual powers and the headroom constraints to the set points. The headroom constraints keep each component's set point and bids within its power, ramp and (battery) energy limits; the steam balance, the curves and the inter-temporal constraints are imposed on the expected dispatch only, not on every other deployment outcome.
8. **Energy payment of Eq. (4).** Eq. (4) is written for a generic reserve. This implementation multiplies the energy payment by a sign $`\sigma_s`$ (`Service.energy_sign`): $`+1`$ for upward services and $`-1`$ for downward services, whose deployed energy is absorbed; set it to $`+1`$ to apply Eq. (4) literally. In `dispatch` mode the deployed energy already changes the actual powers and hence the grid exchange of Eqs. (7)–(8), which Eq. (2) settles at $`\lambda^{E}_t`$; the energy payment is therefore not added a second time unless `Market.energy_payment_in_dispatch` is set.
9. **Symmetric regulation products.** A market with one regulation product is entered as an up and a down service on the same product, each with half of the product's payments (`price_share` scales the capacity and the mileage payment) and equal bids by Eq. (44) (`balance_with`, declared on either side), so that the product is paid once. Separate regulation-up and regulation-down products are paired only for generating their series together (`pair_with`), without equal bids. Products are listed per ISO in `nuclear_h2.services` (Supplementary Table 6).
10. **Regulation mileage.** Where a market pays for mileage, a service with `mileage=True` earns in addition $`\Delta t\,b_{t,s}w_{t,s}\,\phi_s M_{t,s}P_{t,s}\lambda^{mil}_{t,s}`$ ($`\phi_s`$ = `price_share`), with the mileage price $`\lambda^{mil}_{t,s}`$ from `TimeSeries.mileage_price` (no mileage price: no term).
11. **Target capacity factor.** When `target_capacity_factor` is given, the model adds $`\sum_t\Delta t\,p^{elec}_t\ge CF^{H_2}_{target}\,P^{elec}_{max}\,|T|\,\Delta t`$.
12. **Grid limits.** $`p^{sale}_t`$ and $`p^{buy}_t`$ are bounded by `export_max_mw` and `import_max_mw` (the bounds on $`p^{IES}`$ in Supplementary Table 4).
13. **Horizons shorter than a year.** Eq. (6) scales capital to the horizon; `Operation.annual` scales every horizon total by $`8760/(|T|\Delta t)`$.
14. **Two hydrogen credits.** $`\lambda^{H_2,sub}`$ in Eq. (3) shapes the operation and sizing; the TEA pays its own hydrogen credit (`Credits.hydrogen`) on the produced kilograms in its credit years.

## Techno-economic analysis

The TEA takes one optimized year of operation and evaluates a project over its life. Timeline: index $`t=0,\dots,C+N-1`$, with $`C`$ construction years (capital outlays only) followed by $`N`$ operating years. Every operating year repeats the annual operation; optionally a second operation of the same built plant (re-dispatched without the hydrogen credit, with the capacities of the first optimization held fixed) is used from the year after the hydrogen credit ends.

Net present value:

```math
\mathrm{NPV}=\sum_{t=0}^{N}\frac{\mathrm{CF}_{t}}{(1+r)^{t}} \qquad (47)
```

After-tax cash flow of an operating year, with $`R_t`$ revenue, $`C_{OPEX,t}`$ operating expenditure, $`D_t`$ depreciation and $`\tau`$ the corporate tax rate:

```math
\mathrm{CF}_{t}=\left(R_{t}-C_{OPEX,t}-D_{t}\right)(1-\tau)+D_{t} \qquad (48)
```

In this implementation, capital outlays (construction and replacements) are subtracted in the years they occur, and production and investment credits are added after tax (or, with `Credits.taxable`, as taxable revenue). The internal rate of return is a rate at which Eq. (47) is zero (`finance.irr` searches outward from zero); the payback period counts operating years until the cumulative cash flow turns non-negative.

**Capital.** Each component cost is scaled from a reference size:

```math
C_{adj}=C_{ref}\left(\frac{S_{actual}}{S_{ref}}\right)^{\alpha} \qquad (49)
```

The article derives $`\alpha`$ from a learning rate; here $`\alpha=1+\log_2(1-LR)`$ (the cost per unit of size falls by the fraction $`LR`$ per doubling of size), or $`\alpha`$ is given directly. Costs are paid by a payment schedule per component (`CapexItem.schedule`, required): shares by year offset, $`-C,\dots,-1`$ for the construction years and 0 for the first operating year. Replacements are outlays in given operating years, as a fraction of the item's cost or a fixed amount, charged in the cases that build the item or in the cases listed for them, for example components of an existing reactor in Cases 1–2 (`Replacement`).

**Operating expenditure.** Fixed O&M per unit of capacity per year, or as a fraction of an item's capital cost (its cost basis, also when the capital is sunk, as for an existing reactor), escalated by an annual rate and charged in the cases it lists (`FixedCost`); variable O&M from the operation (Eq. 5 terms), the reactor's variable O&M and fuel per MWh. Fuel is charged per MWh of electricity generated (`fuel_basis="generation"`), or per MWh of reactor energy output expressed in electrical terms (`"reactor"`: reactor heat times the turbine's conversion at full steam).

**Depreciation.** MACRS with the half-year convention: declining balance (factor 2 for recovery classes up to 10 years, 1.5 above) switching to straight line; `finance.macrs_schedule` computes the fractions, and each capital item names its recovery class (`CapexItem.macrs_years`). Replacements are depreciated with their item's class from the year they are made.

**Federal credits.** The article's Eq. (50) gives the hourly zero-emission nuclear production credit (Section 45U) as a function of the market price with the statutory amounts; it is written here with parameters, a maximum credit $`c^{45U}_{max}`$ and two price thresholds $`P_{lo}<P_{hi}`$:

```math
C_{45U}(P_{market})=\begin{cases}c^{45U}_{max} & P_{market}<P_{lo}\\[2pt] c^{45U}_{max}\left(1-\dfrac{P_{market}-P_{lo}}{P_{hi}-P_{lo}}\right) & P_{lo}\le P_{market}\le P_{hi}\\[2pt] 0 & P_{market}>P_{hi}\end{cases} \qquad (50)
```

It is paid on the hourly energy (turbine generation, or energy sold) in the calendar years of the credit (`Credit45U`). The clean-electricity production credit (Section 45Y) is a rate per MWh for the first years of operation (`CreditPerUnit`). The clean-electricity investment credit (Section 48E) is a rate times the capital of eligible items (the article applies it to the nuclear generation equipment only), received in the first operating year, and reduces the depreciable basis; the article's Eq. (51) states the statutory reduction fraction, written here as $`\beta`$ (`Credit48E.basis_reduction`):

```math
\mathrm{DepreciableBasis}_{adj}=\mathrm{CAPEX}_{original}-\beta\cdot\mathrm{ITC}_{value} \qquad (51)
```

45Y and 48E are alternatives. A generic hydrogen production credit, modeled after Section 45V, pays a rate per kilogram for the first years of operation (`Credits.hydrogen`). As in the article, an integrated plant may combine the hydrogen-related and the nuclear-related credits.

**Revenue and opportunity cost.** Electricity, hydrogen and ancillary-service revenues come from the operation. Electricity or steam diverted to hydrogen production forgoes electricity sales; the operation model captures this through Eqs. (7)–(9), and the steam diverted to HTE is valued at the turbine's conversion and the hourly price for the levelized cost of hydrogen.

**Levelized costs** (separate accounting for each subsystem; numerators and denominators discounted at $`r`$, before tax):

```math
\mathrm{LCOE}=\frac{\mathrm{PV}\left(\mathrm{NuclearCAPEX}+\mathrm{NuclearOPEX}-\mathrm{TurbineASRevenue}\right)}{\mathrm{PV}\left(\mathrm{TotalElectricityGeneration}\right)} \qquad (52)
```

```math
\mathrm{LCOH}=\frac{\mathrm{PV}\left(\mathrm{CAPEX}_{H_2}+\mathrm{OPEX}_{H_2}+\mathrm{Elec.Cost}_{LCOE}+\mathrm{HTECost}-\mathrm{ASRevenue}_{H_2}\right)}{\mathrm{PV}\left(\mathrm{Total\,H_2\,Production}\right)} \qquad (53)
```

```math
\mathrm{LCOS}=\frac{\mathrm{PV}\left(\mathrm{BatteryCAPEX}+\mathrm{BatteryOPEX}-\mathrm{BatteryASRevenue}\right)}{\mathrm{PV}\left(\mathrm{TotalEnergyDischarged}\right)} \qquad (54)
```

Mapping in `tea.evaluate`: each capital item and fixed cost belongs to a group (`nuclear`, `hydrogen`, `battery`, or `shared`, which enters the NPV only). NuclearOPEX is the reactor's fixed O&M, variable O&M, fuel and turbine variable O&M; $`\mathrm{Elec.Cost}_{LCOE}`$ is the electrolyzer's electricity at the LCOE of Eq. (52); HTECost is the steam diverted to HTE valued at the turbine's conversion and the hourly electricity price; AS revenues are those of the turbine, the electrolyzer and the battery. A levelized cost can be negative when a subsystem's service revenue exceeds its costs.

Capital recovery factor (for the annualized capital costs of Eq. 6):

```math
\mathrm{CRF}=\frac{r(1+r)^{L}}{(1+r)^{L}-1} \qquad (55)
```

**Case studies.** Case 1: an existing reactor, business as usual (all power sold, no hydrogen), with and without 45U. Case 2: the existing reactor retrofitted with hydrogen production and battery storage, nuclear capital sunk, with and without 45U. Cases 3 and 4: a new integrated system with all capital, for two plant lifetimes, each without a clean-electricity credit, with 45Y or with 48E. The hydrogen production credit, when configured, applies in every case with hydrogen production. See [scenarios.md](scenarios.md).

## Synthetic market data

The article describes how ancillary-service capacity prices are generated from the hourly energy price, with $`U`$ a uniform and $`N`$ a normal distribution and a random price spike:

```math
\mathrm{Price}_{AS}=\max\left(0,\ \mathrm{LMP}\times U(a,b)+N\left(0,\sigma^{2}\right)+\mathrm{Spike}\right) \qquad (56)
```

The spike adds a uniform amount between two bounds with a fixed hourly probability. In `nuclear_h2.synthetic` the bounds $`a,b`$, the noise $`\sigma`$, the probability and the spike range are settings (`AsPriceSettings`) with this package's own defaults. The same module generates, in the forms the article describes, paired regulation deployment factors (a smoothed random series with intra-day and weekly patterns, a directional split and a cap on up plus down), Bernoulli-gated reserve deployment with uniform magnitudes, mileage factors with Gaussian noise around a base value, and performance factors from a truncated normal distribution.

## Flexibility indicators

Definitions used by `results.flexibility_metrics` (net grid output $`p^{IES}_t`$):

| Indicator | Definition |
|---|---|
| output range | $`\max_t p^{IES}_t-\min_t p^{IES}_t`$ |
| coefficient of variation | standard deviation of $`p^{IES}_t`$ over its mean |
| ramps | $`(p^{IES}_{t}-p^{IES}_{t-1})/\Delta t`$: maximum up, maximum down, mean absolute value |
| ramp events | hours with an absolute ramp above a threshold (default 5 MW/h) |
| turbine range | $`\max_t p^{turb}_t-\min_t p^{turb}_t`$ |
| electrolyzer capacity factor | $`\sum_t p^{elec}_t/(P^{elec}_{max}\lvert T\rvert)`$; on-hours share; number of starts |
| deployed AS energy | $`\sum_{t,s,c}\Delta t\,b^{c}_{t,s}w_{t,s}\delta_{t,s}`$, also as a share of generation |

## Scope stated in the article

- Electricity and hydrogen prices are exogenous: the plant is a price taker, and price formation and feedback between coupled electricity and hydrogen markets are not modeled.
- Capacity-market revenues and capacity accreditation are not represented.
- Ancillary-service deployment, regulation mileage and performance factors are generated series, because full-year, service-level public records at that resolution are not available.
- The TEA evaluates the optimized annual operation over the project life.
