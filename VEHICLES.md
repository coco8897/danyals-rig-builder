# Vehicles: cars, trucks, tanks, motorcycles and physics

The vehicle rig covers everything from a hatchback to an 8-wheel truck, a
tank or a motorcycle, drives with the keyboard, and has a physics pass that
makes it react to the ground: suspension, body roll, jumps, landings, even
rollovers.

This guide covers building the rig, driving it, simulating physics, adding
moving parts like turrets and excavator arms, towing trailers, crashing
into walls with real dents, and cargo that shakes with the ride.

---

## Build the rig

Switch the Rig Builder to **Vehicle**.

| Option | What it does |
|---|---|
| **Wheels** | 4 (a car), 6 or 8 (trucks, APCs). Changing it adds or removes the middle-axle guides straight away |
| **Steer 2nd axle** | Big trucks steer the second axle too, at about half the angle |
| **Tracked (tank treads)** | No steered wheels: a tread loop wraps each side's road wheels, and the tank skid-steers |
| **Spokes / Suspension / Per-spoke ctrls** | Tyre detail, suspension travel with springs, and a control per tyre spoke for hand-squashing |
| **Springs** | Per axle: coil-over, leaf spring + shock, or none, and whether it's a solid axle (see [Springs](#springs)) |

1. **Create Guides** and drag the left-side and centre guides onto your
   model. The right side mirrors on build.
2. **Wheels:** each wheel guide is a circle: the tyre, seen side on. Put its
   centre on the rim centre and set its **Radius** in the channel box until
   the circle matches the tyre. Every axle has its own radius (big back
   wheels, a small middle axle, a stylised car), and moving a guide never
   changes its size. Quicker: select the tyre meshes (one per axle is
   enough, either side) and click **Fit Wheels to Selected Tyres**.
3. **Build Vehicle Rig**. If a tyre won't sit on the ground (Y = 0) when the
   car is at rest, the Script Editor says which one and by how much: below
   the ground it squashes, above it floats. Move the car or the guide.
4. Select each car part and **Bind Selected Mesh to Part** (Body, Tire, Rim,
   Door, Hood, Trunk, Spring, and the leaf spring parts). It finds the right
   wheel or door by itself.

**Save Guides...** keeps every vehicle guide in a .json file (positions,
wheel sizes, springs, tank wheels, tread thickness) and **Load Guides...**
brings them back, in this scene or another, with as many axles as the file.

Guides from before 1.0.3 set the tyre size from the guide's height. Click
**Create Guides** in that scene once: the wheel guides get their circle and
a Radius equal to their old height, so nothing changes size.

**Tank treads:** the rig builds a loop of link joints round each side's road
wheels. The loop hugs the wheels, bends up over a road wheel that rises on a
bump, and the links run with the distance travelled (the outer tread runs
faster in a turn, and turning on the spot counter-rotates them).

To put your tread link model on it:

1. Click **Instance Link Mesh** with nothing selected. The warning tells you
   how far apart the links are, so you know how long to model one.
2. Model ONE link at the world origin: lying flat, running along **+Z**
   (the direction the tread moves), with its pin / axle along **X**. Its
   centre is where the link sits on the loop.
3. Select it and click **Instance Link Mesh**. It's instanced onto every
   link of both treads and the original is hidden. Edit the original (it's
   an instance) and every link updates.

Tread modelled as one piece (a solid loop, not links)? Select it and bind
it as **Tread** instead: it's skinned to the link joints of its side (or
both sides, if both loops are one mesh) and flows round the wheels.

### Tank wheels: any layout

The Wheels count gives a tank 2 to 4 evenly spaced road wheels a side. A
real or cartoon tank often has more, in different sizes, with rollers on
top and gears in between. Open **Tank Wheels** (tick **Tracked** first):

1. Select the tank's wheel meshes and its tread, and click **Fit Wheels
   from Selected Meshes**. A mesh can hold one wheel, both wheels of a pair
   (left and right) or a wheel with its hub nut. You get one wheel guide
   per wheel, on its centre and sized to it. The rest of the guides are
   scaled to the tank and the chassis guide moves to its middle.
2. Each wheel's job is guessed from the layout and shown by its circle's
   colour. Fix any that are wrong: select the guides, pick the job, click
   **Set on Selected**.

| Job | What it does |
|---|---|
| **Road wheel** (orange) | On the ground, on its own suspension. The tread runs under it |
| **Roller** (cyan) | On the hull. The tread wraps over it |
| **Gear** (grey) | Inside the loop. It only spins; **Reverse spin** turns it the other way, like a gear meshed with the wheel next to it |

3. **Tread thickness** is how thick the tread is under the road wheels
   (Fit measures it from the tread mesh). The road wheels ride that far
   above the ground, and the tread runs round them at half of it.
4. **Build Vehicle Rig.** Up to 12 road wheels a side, any number of
   rollers and gears. The Wheels count is ignored while tank wheels exist.

What you get:

* The tread wraps the road wheels and rollers tight, like a rubber band,
  and its bottom run lies flat on the ground even if a road wheel was
  modelled a little high or low. Road wheels ride bumps and push the tread
  with them. Rollers and gears stay on the hull.
* Every wheel spins at the tread's speed for its own size, so small wheels
  spin faster than big ones, and turning skid-steers the two sides.
* Road wheels are the axles `LF`, `LM1`, `LM2` ... `LB` (so Drive Mode,
  physics and terrain work as always). Rollers and gears are `LX01`, `LX02`
  ... front to back, each with a `hub_BIND_JNT` to bind the rim to.
* Controls and joints are drawn at the tank's size.

Bind the rims as **Rim**: a mesh holding several wheels (even both sides)
binds each piece to its own wheel, and Rim finds rollers and gears too.

---

## Springs

Open **Springs** under the Wheels row. Every axle picks its own suspension,
so a pickup can have coil-overs at the front and leaf springs at the back.

| Type | What you get | Bind as |
|---|---|---|
| **Coil-over** (default) | A coil spring + damper from the spring guide down to the wheel. Most cars | Spring |
| **Leaf spring + shock** | A leaf that bends as the axle moves, on a swinging shackle, plus a shock absorber that telescopes. Trucks, pickups, jeeps, old cars | Leaf Spring, Shackle, Shock Body, Shock Rod |
| **None** | The wheel still travels, with no spring parts | |

**Solid axle** (a tick per axle): one beam ties the two wheels. When one
wheel rides up a bump the axle tilts and both wheels lean with it. It builds
`<axle>_axle_BIND_JNT` (`B_axle_BIND_JNT` for the back axle): bind the axle
tube and differential to it as **Axle**. Off = each wheel moves on its own.

**Placing a leaf spring.** Picking Leaf spring adds 6 orange guides for that
axle, placed from its wheel guide. Drag them onto the model:

| Guide | Where |
|---|---|
| `L_<axle>LeafFront` | The leaf's front eye, where it bolts to the frame hanger |
| `L_<axle>LeafSeat` | The middle of the leaf, where it sits on the axle (the U-bolts) |
| `L_<axle>LeafRear` | The leaf's rear eye, where it hangs from the shackle |
| `L_<axle>ShacklePin` | The shackle's pin on the frame, above the rear eye |
| `L_<axle>ShockTop` / `ShockBottom` | The shock absorber's frame and axle mounts |

`<axle>` is front, mid1, mid2 or back. The coil-over guide hides on a leaf
axle. The right side mirrors as usual.

**How it moves.** The leaf is a curve through the front eye, the seat and
the rear eye, with 7 joints along it (`LB_leaf_01` to `07_BIND_JNT`). As the
axle rises the leaf flattens, and as it drops the leaf arches. A real leaf
can't stretch, so the shackle swings to make room: the rig works out the
angles when it builds, and the leaf keeps its length all the way from full
droop (wheels hanging in the air) to well past full bump. Skin the leaf
pack to the 7 joints (**Leaf Spring** does this for the nearest side) and
it bends with them.

Tanks keep independent coil-overs on their road wheels.

---

## Tyre tracks and burnouts

**Bake Tyre Tracks** lays the marks the wheels left over the timeline.

A tyre that is simply rolling leaves nothing on tarmac, so by default only
the **driven wheels** mark, and only where they were really **sliding**:

| It marks when | Which looks like |
|---|---|
| The wheel is going sideways to where it points | A drift, a handbrake turn, a spin |
| The tyre turns further than the road goes by | Wheelspin off the line, a burnout |
| The road goes by and the tyre doesn't | A locked wheel under braking |

Everything else stays clean, so a normal drive and a tidy corner leave no
marks at all. Set **All wheels** and untick **Only when sliding** to mark
everywhere the tyres touch, which is what sand, mud and snow want.

Each mark is a flat ribbon under the contact patch that follows wherever
the wheel really went: a drive, a physics bake or your own keys, flat
ground or terrain. Where the tyre was off the ground there is a gap,
because it wasn't marking anything, and a leaning bike leaves a narrower
mark, because it is riding the edge of its tyre.

They are ordinary meshes under `TYRE_TRACKS_GRP`, with UVs running along
their length and a plain dark shader on them, so you can texture, fade or
delete any of them. Baking again adds another pass beside the first
(handy for a second lap); **Clear Tracks** removes the lot.

A tank marks with the front and back road wheel of each side, since the
rest of the side runs in the same line (and it slides every time it
skid-steers). A bike marks with its back wheel, or both on All wheels.

Drive Mode writes down how much the tyres were sliding each frame on
`C_chassis_CTRL.tyreSlip`, so a bake after a drive knows exactly where the
handbrake came on. Hand-keyed or simulated animation has no such record,
so the bake works it out from the motion itself.

**Burnout** lights up the driven wheels from the current frame:

| Setting | What it does |
|---|---|
| **Rear / Front / All wheels** | Which wheels get the power. A bike's back wheel counts as rear |
| **frames** | How long it lasts |

The wheels spin far faster than the vehicle moves (that is what a burnout
is: the tyre is turning, the ground is not going by), it creeps forward
and starts to hook up at the end, the body squats onto the back wheels,
and the wide dark marks are baked for you. **Clear Burnout** takes the
keys back off the wheels and the body; the marks stay until you clear
those too.

Both work on anything with wheels: a car, a truck, a tank or a bike.

---

## Motorcycles

Switch the Rig Builder to **Motorcycle**. A bike is a vehicle with its two
wheels in a line, so it is built with the same controls as a car and
everything that works on a car works on it: Drive Mode, terrain, export,
bind, crash.

1. **Create Bike Guides**: seven locators, all on the centre line.
2. **Fit Guides to Selected Model**: select your bike's meshes and the
   guides size themselves to it, then snap to its wheels. Or place them by
   hand; **Fit Wheels to Selected Tyres** does just the two wheels (their
   centres AND their sizes) and moves the rest with them.
3. **Build Motorcycle Rig**.

| Guide | Put it on |
|---|---|
| **C_frame** | The bike's centre. The frame joint sits here and you drive from this control |
| **C_frontWheel** | The front axle. Its circle is the tyre: set its **Radius** in the channel box |
| **C_backWheel** | The back axle, with its own radius (rear tyres are usually fatter) |
| **C_steeringHead** | The top of the steering head, where the fork meets the frame |
| **C_handlebar** | The bar centre, where the steering control is drawn |
| **C_swingarmPivot** | Where the swingarm bolts to the frame |
| **C_shockTop** | The rear shock's top mount on the frame |

**The rake comes from the guides.** The line from the steering head down to
the front axle *is* the steering axis, so laying the head back gives you a
chopper and standing it up gives you a sportbike. Nothing to type.

What the rig does:

* **The fork steers** about that axis, carrying the wheel, the bars and the
  fork tubes, and its suspension **slides along the fork** the way a
  telescopic fork really works (the tyre still lands exactly on the ground
  at any rake).
* **The swingarm swings** from its pivot and always aims at the rear axle,
  with a coil-over shock from the frame down to the wheel.
* **Both tyres ride the ground** on their own, over a ground mesh too.
* **The bike leans.** `C_chassis_CTRL.lean` tips the whole bike about the
  line where the tyres touch the ground, so they stay planted instead of
  sliding out from under it. `leanAmount` scales it (0 = off).
* The frame **pitches and bobs** on its suspension. It doesn't roll: a bike
  leans instead.

**Driving it:** Drive Mode works exactly as it does on a car, and leans the
bike into every corner by itself, further the faster and tighter you go,
by the same angle a real rider would use (`atan(speed x turn rate / g)`),
eased in and out and capped at 45 degrees. The lean is keyed with the rest
of the drive, so you can scale it afterwards on `leanAmount`, or key `lean`
by hand on top.

**Binding:** Body (the frame), Fork (both tubes, so the lower one slides
inside the upper), Handlebar, Swingarm, Rim and Tire.

---

## Ground

Select your terrain mesh and click **Assign Selected Mesh as Ground**. Each
tyre follows the surface under it and squashes against it. Without a
ground mesh the rig uses the flat ground locator at Y = 0.

The suspension alone can only lift a wheel by **maxCompression**. On a hill
bigger than that, the whole vehicle has to climb, so:

* **Drive Mode** lifts and tilts the hull with the terrain as you drive
  (keyed on `C_global_CTRL` translateY, rotateX and rotateZ).
* **Simulate Physics** does it with real weight, momentum and bounce.
* **Hand-keyed** animation: key the height and tilt yourself, or run
  Simulate Physics over it. Raising or tilting the vehicle keeps the tyres
  on the ground (the wheels reach down, up to maxDroop).

---

## Drive Mode

Click **Drive Mode (WASD)**, click the little panel, and drive. The drive
is recorded from the current frame and keyed when you press Esc (the
timeline stays put while you drive, which keeps even a tank with trailers
smooth). Cached Playback is paused while you drive and comes back on after.

| Key | Car | Tank | Motorcycle |
|---|---|---|---|
| **W / S** | Throttle / brake, then reverse | Same | Same |
| **A / D** | Steer left / right (less lock at speed) | Turn the hull, even standing still | Steer, and the bike leans into the corner |
| **Space** | Handbrake: the rear breaks loose and slides, grip returns when you let go | Same | Same |
| **Esc** | Stop | Stop | Stop |

The pedals ease in and out, so keyboard driving doesn't look digital.
Tune the feel in the panel: Drift, Top speed, Accel, Steer lock, Drift grip
(how far the back slides out), Grip return (how fast it catches), Pedal
response, and the body bounce settings.

**Clear Drive Animation** removes the drive (and any physics on top of it)
so you can take another pass.

---

## Simulate Physics

This is the tyFlow-style part. Drive a path (or keyframe `C_global_CTRL`
moving and turning), then click **Simulate Physics** (in the Vehicle section
or on the Drive panel). The car follows your path, and physics does
everything the ground does to it:

* each wheel springs and damps on its own
* the body rolls out in turns, dives when braking, squats when accelerating
* ramps and crests launch it: it flies, lands, compresses and rebounds (you
  can't steer in the air, so it keeps its momentum and rejoins the path)
* a top-heavy car can tip over in a hard turn

It bakes keys over the playback range. **Clear Simulation** puts your
original path back exactly, and simulating again always starts from that
original path. Works for 4, 6 and 8 wheels and tanks.

### Settings (on `C_chassis_CTRL`, PHYSICS section)

| Setting | What it does |
|---|---|
| **simSuspensionHz** | Suspension stiffness as a bounce frequency: about 1 soft offroad, 2 road car, 3+ race car |
| **simDamping** | Low = bouncy, 1 = settles without overshoot |
| **simGrip** | Low = slides like ice, high = sticks and leans hard |
| **simCenterOfMass** | Height of the mass above the hubs, in wheel radii. Higher = more roll and dive, and eventually rollovers |
| **simPathFollow** | How tightly the car sticks to your path while its wheels are down |
| **simSubsteps** | Physics steps per frame. Raise for very fast cars or very stiff springs |
| **simStartDrop** | Drop the car from this height on the first frame |
| **simPathSmoothing** | Irons out keyboard jitter in the path before the car follows it |

### Manual override

* **simPhysicsBlend** (keyable): 1 = full physics, 0 = the car sits exactly on
  your keyframes, including any height or tilt you keyed. Key it to hand
  control over mid-shot: animate a stunt by hand with the blend at 0, then
  key it to 1 and physics takes over from exactly where your keys left off.
* **simKeepHeight / simKeepPitch / simKeepRoll**: keep your own keys on that
  channel, simulate the rest.
* **simKeepPath**: keep your exact path (no sliding wide, no drift after a
  jump), and let physics add only height, pitch, roll and suspension.

---

## Vehicle Parts (turrets, arms, hinges)

Open **Vehicle Parts** in the Vehicle section.

| Preset | Parts |
|---|---|
| **Tank Turret** | A turret that turns on the hull and a barrel that elevates (limited), both able to aim at a target |
| **Excavator Arm** | Swinging cab, boom, stick and bucket, each with limits, plus three hydraulic rams that stay connected |
| **Tipper Bed** | A dump-truck bed hinged at the back |

1. **Add Preset Guides** (or **Add Hinge at Selection** for your own part:
   name, axis, what it's on, optional limits, optional aiming).
2. Move the part guides onto your model's pivots.
3. **Build Parts**.

Every part is a control (`C_<name>_part_CTRL`) that turns about one axis
within its limits, driving a joint you can skin to. Parts ride the body, so
they follow the suspension and the physics.

* **Aiming:** set **aimAtTarget** to 1 on the turret and barrel controls and
  move `C_partAim_LOC`. 0.5 blends between your animation and aiming.
* **Rams:** the cylinder and rod joints stay pointed at each other and slide
  in and out as the arm moves. Skin the ram meshes to them.
* **Rebuilding the vehicle:** the part guides are kept, and Build Vehicle Rig
  builds the parts again automatically.

---

## Trailers

Open **Trailers** in the Vehicle section. You can tow up to 3 trailers in a
chain (a road train), each with 1 to 3 axles. A tank can tow too.

1. Set **Axles** and click **Add Trailer Guides**. Guides appear behind the
   vehicle (or behind the last trailer):
   * `T1_hitch_GUIDE`: put it on the tow point (the ball or pin the trailer
     pivots on).
   * `T1_axle1_GUIDE`, `T1_axle2_GUIDE` ...: put each on that axle's **left**
     wheel centre (the right side mirrors). Each is a circle, the tyre seen
     side on: set its **Radius** in the channel box until the circle matches
     the tyre. Every axle has its own size, so a trailer can run smaller
     wheels than the truck, and moving a guide never resizes the wheel.
     Quicker: select the tyre meshes (the truck's and the trailers', one
     tyre per axle is enough, or both tyres in one mesh) and click **Fit
     Wheels to Selected Tyres**: each tyre fits the wheel guide nearest it.
2. **Build Trailers**. If a trailer tyre won't sit on the ground (Y = 0) at
   rest, the Script Editor says which axle guide and by how much.
3. Bind your meshes with **Bind Selected Mesh to Part**: **Trailer** for the
   trailer body, **Hitch** for a tow ball or drawbar on the vehicle, and
   **Tire / Rim / Spring** work on trailer wheels too.

How they move:

* The trailer swings behind its hitch like a real one. In a turn it cuts
  the corner (its wheels run inside the vehicle's), and reversing with a
  kink jackknifes it, up to **maxSwing** (on `T1_trailer_CTRL`, default 80).
* Trailer wheels roll, have suspension, follow the ground mesh and squash
  against it, and the trailer pitches and rolls over the terrain from its
  hitch.
* **Drive Mode** moves the trailers as you drive. **Simulate Physics** bakes
  them after the vehicle, and **Clear Simulation** puts them back on your
  original path.
* Hand-keyed the vehicle? Click **Bake Trailers (timeline)**. Bake again
  whenever you change the vehicle's animation.
* `T1_trailer_CTRL` rotates on top of the follow, so you can push a swing
  further or straighten one up by hand.

**Rebuilding the vehicle:** the trailer guides are kept, and Build Vehicle
Rig builds the trailers again. **Save Guides...** keeps them in the file
with the vehicle's guides.

Trailer guides from before 1.0.3 sized the wheels from the guide's height.
They get their circle, with a Radius equal to that height, the next time
you add a trailer or build, so nothing changes size.

---

## Cargo (roof racks and loose parts)

Open **Cargo** in the Vehicle section. Spare wheels, jerry cans, lights and
bags on a roof rack move with the ride instead of being glued to the body.

1. Each item must be its own mesh. If it was modelled into the body, select
   its faces and **Mesh > Extract** (or Separate, then Combine each item's
   pieces back into one mesh).
2. Bind the body to the rig first (the item rides whatever it was bound
   to: the body, the hood, a trailer).
3. Select the items and click **Add Selected as Cargo**. Each gets a joint
   and a control pivoting at its base, where it's strapped down.
4. Drive, Simulate Physics or hand-key the vehicle. Drive Mode and Simulate
   Physics bake the cargo when they finish; after hand-keying click **Bake
   Cargo**.

What you get: an item leans back when you accelerate, forward when you
brake (with a wobble as it settles) and out in turns, hops over bumps and
landings, and rattles, a little when idling and more the faster you go. A
tall, narrow item tips further than a low, wide one.

| Setting (on each `<item>_cargo_CTRL`) | What it does |
|---|---|
| **stiffness** | How tightly it's strapped (higher = leans less and snaps back faster) |
| **damping** | 0 = wobbles for a long time, 1 = settles at once |
| **maxLean** | The most it ever leans, in degrees |
| **rattle** | Vibration (0 = none) |
| **bounce** | How much it hops over bumps |
| **physics** | Keyable. Fades the whole bake in and out |

Change a setting, then **Bake Cargo** again. The control itself animates on
top of the bake (a strap coming loose, a hand-keyed shove). **Select
Controls** picks every item's control for tuning. Build Vehicle Rig brings
cargo back with its tuning, and the cargo joints export with the vehicle.

---

## Crash damage

Open **Crash Damage** in the Vehicle section. Walls, poles, barriers and
parked cars become solid, and the car dents where it hits them. The ground
counts too: a rollover crushes the roof, a hard landing that bottoms out
dents the underside, a nose-first landing folds the bumper.

1. Select the obstacle meshes and click **Add Selected as Obstacles**. Not
   the ground mesh: the wheels already drive on that. Tick **They dent
   too** first for another car or anything else that should crumple: it
   gets dented where it's hit, and the two crumple zones share the crush,
   so both fronts fold and end up against each other.
2. Bind the car's meshes first (**Bind Selected Mesh to Part**). Everything
   bound to the vehicle dents (body, hood, doors, trunk, trailers), except
   the wheels.
3. Crash:
   * **Drive Mode**: the car stops at the obstacle instead of driving
     through. The faster the hit, the deeper the front crushes and the
     longer it takes to stop; a glancing hit knocks the car sideways and
     spins it. The dents are baked when you press Esc.
   * **Simulate Physics**: the same on a driven or keyed path. After a real
     hit the driver lets go of the path and brakes (**crashStopsPath**).
     The body lands on the ground instead of sinking through it, so a car
     that rolls ends up on its crushed roof. Ground hits don't count as a
     crash: the driver keeps going after a hard landing.
   * **Hand-keyed**: key the car into the wall, then **Bake Crash Damage**.

What you get: the metal that touched is pushed in (a pole leaves a
pole-shaped notch and the car wraps round it), the metal around it is
dragged along and buckles, and each dent grows at the moment of impact.

| Setting (C_chassis_CTRL > CRASH) | What it does |
|---|---|
| **crashDamage** | Keyable. Scales every dent: 0 = showroom, 1 = as crashed |
| **crashStrength** | The g-force a full-width hit stops the car with (default 12). Lower = softer car: deeper crush, longer stop |
| **crashMaxCrush** | Deepest crush as a share of the car's length (0.2). Past it the car is solid and stops dead |
| **crashSpread** | How far a dent drags the metal around it, as a share of the car's length |
| **crashCrumple** | Wrinkles and buckles in the dented metal. 0 = smooth dents |
| **crashBounce** | How much the car bounces back off what it hit |
| **crashStopsPath** | Simulate Physics: let go of the path after a hit |
| **crashGround** | The ground dents the car too (your ground mesh, or the flat ground). On by default |

Good to know:

* The dents are standard blendShapes in front of the skin, keyed per
  impact, so the file opens anywhere with no plugin. **Clear Damage**
  removes them; any new drive, simulation or Clear Drive Animation throws
  the old dents away and bakes new ones for the new motion.
* Change a CRASH setting, then drive, simulate or **Bake Crash Damage**
  again to see it.
* **Moving obstacles**: key an obstacle (a wrecking ball, a battering ram,
  another car) and **Simulate Physics**: it follows its animation, shoves
  the vehicle when it hits it and dents it, even if the vehicle is just
  parked. A skinned obstacle (a character) stays where it is on the first
  frame of the simulation. Drive Mode treats every obstacle as standing
  still; the damage bake follows them all.
* The other car is an obstacle, not a second rig: it dents, but only
  moves if you animate it.
* A single plane works as a wall from whichever side the car starts on.
* Drive Mode keeps the car on the terrain, so there only obstacles dent it;
  ground damage comes from Simulate Physics and Bake Crash Damage.
* Metal that already touches the ground when the car sits at rest (a low
  lip, mudflaps) never dents on it, and a light scrape isn't a dent.
* The ground dents the car even with no obstacles added, as long as its
  meshes are bound. Turn **crashGround** off if you don't want that.
* Game export ships the undamaged car.

---

## Good to know

* **Physics only collides with the ground mesh.** The car won't hit walls,
  props or other cars.
* **Trailers follow, they don't push:** a heavy trailer doesn't slow the
  vehicle down or shove it around in a simulation, and trailers don't
  collide with the vehicle (maxSwing stands in for that).
* **Parts don't change the physics:** a heavy turret doesn't shift the
  centre of mass. Use simCenterOfMass for top-heavy vehicles.
* **Treads are driven by distance, not simulated as a chain:** they follow
  the road wheels over bumps, but don't sag or flap.
* **Controls are drawn at the vehicle's size:** a tank a tenth the size of
  a car gets controls a tenth the size. The size comes from how far the
  wheels reach, front to back.
* **A motorcycle has no parts, trailers or doors:** it has Drive Mode,
  terrain, crash obstacles and the bind tools. Simulate Physics runs on it
  and bakes its pitch, bob and suspension over the ground, but it treats
  the bike as a two-contact body: it never leans and never falls over, so
  the lean stays Drive Mode's (or yours, on `lean`).
* **A leaned bike's tyres are discs, not rounded:** they stay on the ground
  at any lean, but a real tyre also walks its contact patch sideways as it
  goes over. At 45 degrees or so it reads fine; past that, sell it with the
  camera.
* **A turret tracking a target straight behind it** can spin the long way
  round at that exact moment. Keep aim targets off the dead-rear line.
* **Animating `C_chassis_CTRL` itself** alongside a simulation: the physics
  reads its pose on the first frame only.
* **Slow playback on a tank:** a 6 or 8-wheel tank has hundreds of joints
  (spokes and tread links) and drawing them is what slows the viewport. Hide
  joints while you play (viewport **Show > Joints**), turn on Cached
  Playback, or use fewer spokes.
* **Rigs built with an earlier version** don't get the terrain and tread
  fixes: **Delete Vehicle Rig**, then **Build Vehicle Rig** again from your
  guides, re-bind the meshes and re-assign the ground.

Found something that doesn't behave like a real vehicle? Open a GitHub
Issue with your Maya version and a screenshot.
