# The model

## 1. What is being solved

Let `phi(z, t)` be the red-cell volume fraction (the local hematocrit) on the
axis of a tube, `z` measured upward from the closed bottom, and `A(z)` the
inner cross-sectional area. Blood is treated as a suspension of settling
aggregates in plasma. Nothing enters or leaves the tube, so the cell volume is
conserved:

```
d/dt [ A(z) phi ]  =  d/dz [ A(z) f(phi) ]
```

`f(phi)` is the downward volumetric flux of cells per unit area. This is
Kynch's 1952 theory of batch sedimentation, with the one addition that matters
here: `A` depends on `z`, so the geometry of the tube enters the conservation
law itself rather than being a boundary decoration.

Expanding the right-hand side for a uniform suspension shows what geometry
does:

```
dphi/dt = -f'(phi) dphi/dz - f(phi) * A'(z)/A(z)
```

The second term has no counterpart in a straight tube. Where the tube narrows
downward (`A' > 0`), it is positive: more cells arrive from above than the
shrinking cross-section can pass on, and the suspension *concentrates*, which
slows it further. Where the tube widens downward it is negative and the
suspension *dilutes*, which speeds it up. Both are visible in the shipped
comparison figure.

## 2. The settling flux

A single rouleau of effective diameter `d` settles in still plasma at the
Stokes velocity

```
u0 = k * (rho_rbc - rho_plasma) * g * d^2 / (18 * mu)
```

with `k` a shape factor for the stacked-coin geometry of a rouleau. For
`d = 60 um` this is about 180 mm/h; the particle Reynolds number is ~2e-3, so
creeping flow holds comfortably.

A crowded suspension settles far more slowly, because the plasma displaced by
the falling cells has to flow back up past them. The default law is

```
f(phi) = u0 * phi * (1 - phi/phi_max)^n
```

with `n = 4.65` (Richardson-Zaki) and `phi_max = 0.90` the packed-cell volume
fraction. It vanishes at both ends, which is what produces the two features of
a real ESR tube: a clear plasma column on top and a packed sediment at the
bottom that stops compacting. At `phi = 0.45` the suspension settles at
`(1 - 0.45/0.9)^4.65 = 4 %` of `u0`, i.e. about 7 mm/h -- the normal range.

`richardson-zaki` (`f = u0 phi (1-phi)^n`, cut off at `phi_max`) and `free`
(`f = u0 phi`, no hindrance, for verification) are also available.

**Rouleaux formation.** Erythrocytes have to stack before they settle quickly,
which is why a real ESR curve starts almost flat. The velocity is ramped as
`u0 * (1 - exp(-t/tau))` with `tau` of order 5 minutes.

**Wall drag.** Settling near a wall is retarded by the Faxen factor

```
K = 1 - 2.104 L + 2.089 L^3 - 0.948 L^5,     L = d_aggregate / d_tube
```

evaluated with the *local* diameter, so a tube that narrows also drags more.
It costs ~5 % in a 2.5 mm Westergren tube and ~25 % in a 0.5 mm capillary.

## 3. Geometries with a core

A geometry may have an *inner* wall as well as an outer one -- a cone standing
inside another cone, with the blood filling the annulus between them
(`AnnularCone`). Nothing in the solver changes: it still only asks for the flow
area, which becomes

```
A(z) = pi (r_out(z)^2 - r_in(z)^2)
```

Two things do change, and both matter:

* **The characteristic width is the hydraulic diameter** `D_h = 4A/P`, which for
  an annulus is twice the gap and for a plain tube is still the bore. That is
  what the wall drag and the Boycott gap are evaluated with, so a 1 mm gap
  inside a 60 mm vessel drags like a 2 mm capillary, not like a 60 mm beaker.
* **Both walls are settling surfaces**, which the next section counts.

The gap is specified perpendicular to the wall, so the radial gap is
`gap / cos(angle)`. Below the inner cone's tip the annulus closes and the
section is a plain circle. A configuration where the two cones would meet is
rejected rather than silently producing a zero flow area.

## 4. Inclination -- the Boycott effect

Blood settles faster in a tilted tube: cells clear a layer along the raised
wall, that plasma runs up, and the sediment slides down the lowered wall.
Ponder (1925) and Nakamura & Kuroda (1937) related the rate of clear-fluid
production to the *horizontal projection* of the vessel. For a suspension
column of length `L` in a tube of bore `d` tilted `theta` from vertical, the
axial settling velocity is multiplied by

```
Lambda = cos(theta) + eta * 4 L sin(theta) / (pi d)
```

`eta = 1` is the textbook result, derived for a wide gap where the clear layer
drains instantly. It overpredicts a narrow clinical tube by an order of
magnitude (it makes a 3 degree tilt a fivefold error). The default
`eta = 0.08` is calibrated instead to the clinical rule of thumb that a
3 degree tilt inflates a Westergren reading by roughly 30 %. Set
`efficiency=1.0` for the textbook formula, or `model="none"` to keep only the
`cos(theta)` reduction of gravity.

### Inclined walls, without tilting anything

A wall that is not vertical is a settling surface in exactly the same way: cells
land on its upward-facing side and slide off, clear plasma is released under its
downward-facing side and rises. This is the principle of a lamella settler, and
of the cone-inside-a-cone geometry, and it is why a conical vessel settles
faster than a cylinder standing perfectly upright.

The projected-area argument covers it, and for an axisymmetric wall the
horizontal projection between two heights is exactly the change in the circle it
encloses -- so it needs no angle estimate at all:

```
P_wall = total variation of pi r^2 over the suspended column
```

summed over both walls when there is a core. The two contributions are
projections of different surfaces, so they add:

```
Lambda = cos(theta) + eta * [ 4 L sin(theta) / (pi D_h) + P_wall / A ]
```

`A` is the flow area where the boundary currently sits, which is what turns a
production rate into a boundary speed. A straight tube has `P_wall = 0`, so
nothing about the tilted-tube calibration above changes; set
`BoycottModel(walls=False)` to drop the term entirely.

Both terms are 1-D surrogates for a genuinely 2-D flow. Treat them as "how much
faster, roughly", not as predictions to three digits. In particular the
enhancement is applied along the whole column while the surfaces producing it
may sit in one part of the vessel -- defensible for the *boundary speed*, since
clear fluid produced anywhere in a closed vessel must show up at the boundary,
but it also shifts the concentration profile, which is an approximation.

## 5. The flow field

Once the concentration field is known, the flow is not a matter of opinion.
Nothing enters or leaves, and both phases are incompressible, so the net
volumetric flux through every cross-section is zero:

```
phi * v_cells = (1 - phi) * v_plasma
```

The solver already has the cell flux `f = u_local * psi(phi)`; dividing by each
phase's own volume fraction gives that phase's velocity:

```
v_cells  = f / phi          downward -- what a rouleau actually does
v_plasma = f / (1 - phi)    upward   -- the return flow that hinders it
```

`bloodsed.flows` returns both, plus the plasma volume pushed through each
cross-section in mL/h. That upward plasma is the physical reason a crowded
suspension settles so slowly, and why a throat or a narrow gap throttles the
column above it: the plasma has to get back up through it.

Two checks worth knowing: where the suspension is vanishingly dilute the cells
fall at the free Stokes velocity (hindrance disappears), and in the packed
sediment and the clear plasma both velocities are zero.

## 6. Numerical method

Finite volumes on `n_cells` axial cells. Cell `i` has volume
`V_i = integral A dz` taken from a cached cumulative-volume table, so the cell
volumes sum to the tube volume exactly even across a step in the bore.

```
V_i dphi_i/dt = Q_{i+1/2} - Q_{i-1/2}
Q_k = A_k * u_k * G(phi_above, phi_below)
```

with `Q = 0` at the closed bottom and at the free surface.

`G` is the exact Godunov flux for a unimodal flux function, in supply/demand
form:

```
G = min( demand(phi_above), supply(phi_below) )
demand(phi) = f(phi)   if phi <= phi*, else f_max
supply(phi) = f_max    if phi <= phi*, else f(phi)
```

where `phi*` maximises `f`. This is the same construction as the cell
transmission model in traffic flow, and it is the right one here: `supply`
vanishes at `phi_max`, so a packed cell accepts nothing, sediment forms at
exactly the packing limit, and a constriction jams instead of overfilling.
Each face is additionally capped by what the sender holds and by the room the
receiver has, which keeps `phi` inside `[0, phi_max]` for the flux laws that
jump at the packing limit, without ever discarding cells.

Time stepping is explicit Euler under

```
dt <= CFL * min_i [ V_i / (max(A_{i-1/2}, A_{i+1/2}) * u_max) ]
```

The scheme is monotone and conservative; runs report the relative drift in
total cell volume, which is at round-off (~1e-16).

**Reading the boundary.** The plasma/cell boundary is placed where a sharp
interface would carry the same cell volume as the computed profile, referenced
to the suspension just below the front. That is exact for a shock, resolves
below one cell, and does not climb a staircase as the front crosses cells --
unlike a plain threshold crossing. The anchor is walked down until the profile
stops climbing, so it clears the front whatever its width.

## 7. Verification

`tests/test_solver.py` checks the model against everything with a closed form:

| check | result |
|---|---|
| cell volume conservation | < 1e-12 relative, all geometries |
| `0 <= phi <= phi_max` | holds, including at steps and throats |
| free settling falls at `u0` | within 1 % (first-order smearing of a linear contact) |
| hindered front matches the Kynch shock speed `u0 (1-phi0/phi_max)^n` | within 1e-5 |
| mesh independence, 200 to 1600 cells | identical to 6 significant figures |
| free settling stops when the boundary meets the sediment | at `H (1 - phi0/phi_max)`, exactly |
| frustum, stepped and cylinder volumes | analytic |
| annular flow area, hydraulic diameter, wall projection | analytic |
| the two phases balance, `phi v_cells = (1-phi) v_plasma` | to 1e-15 |
| a vertical annulus settles like a plain tube | within 5 % |

Behavioural checks (direction, not magnitude): a crowded sample settles more
slowly, a downward-narrowing tube concentrates and a widening one dilutes, a
throat throttles the column, and a 3 degree tilt inflates the reading by
30-60 %.

## 8. What the model does not do

* **One dimension.** Convection rolls, the sediment sheet sliding down a
  tilted wall, and any radial structure are outside it. The Boycott effect is
  therefore parameterised rather than resolved: the axial velocities are exact
  for the model, but the two wall layers a tilted tube develops (clear plasma
  running up the raised wall, sediment sliding down the lowered one) are drawn
  schematically in the app, not computed.
* **Annular gaps are treated by their hydraulic diameter**, not resolved across
  the gap. Real lamella settlers also re-suspend sediment when the plates are
  too shallow to shed it, which this model has no way to know about.
* **No compressive sediment rheology.** The packed column stops at `phi_max`
  instead of consolidating under its own weight over hours.
* **Aggregation is a fixed input**, not a kinetic model of fibrinogen-mediated
  rouleaux formation and shear-dependent breakup. `aggregate_diameter_um` is
  the knob standing in for the acute-phase response.
* **Newtonian plasma at rest.** No non-Newtonian whole-blood rheology, no
  yield stress, no temperature dependence beyond the viscosity you supply.
* Predictions are for *understanding and teaching*, not for calibrating a
  clinical instrument.

## References

* Boycott, A. E. (1920). Sedimentation of blood corpuscles. *Nature* 104, 532.
* Ponder, E. (1925). On sedimentation and rouleaux formation.
  *Q. J. Exp. Physiol.* 15, 235-252.
* Nakamura, H. & Kuroda, K. (1937). La cause de l'acceleration de la
  sedimentation des suspensions dans les recipients inclines.
  *Keijo J. Med.* 8, 256-296.
* Kynch, G. J. (1952). A theory of sedimentation. *Trans. Faraday Soc.* 48,
  166-176.
* Richardson, J. F. & Zaki, W. N. (1954). Sedimentation and fluidisation.
  *Trans. Inst. Chem. Eng.* 32, 35-53.
* Happel, J. & Brenner, H. (1983). *Low Reynolds Number Hydrodynamics*
  (wall correction factors).
* Yao, K. M. (1970). Theoretical study of high-rate sedimentation
  (the inclined-plate/lamella settler result the annular geometry rests on).
  *J. Water Pollut. Control Fed.* 42, 218-228.
* Acrivos, A. & Herbolzheimer, E. (1979). Enhanced sedimentation in settling
  tanks with inclined walls. *J. Fluid Mech.* 92, 435-457.
* Bustos, M. C., Concha, F., Burger, R. & Tory, E. M. (1999).
  *Sedimentation and Thickening* (the Kynch conservation law and its Godunov
  discretisation).
* International Council for Standardization in Haematology (2011).
  Recommendations for measurement of erythrocyte sedimentation rate.
  *J. Clin. Pathol.* 64, 671-672.
