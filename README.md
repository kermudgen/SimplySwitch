# Simply Switch

**Switch the active rig with a single click — without ever leaving Pose Mode.**

A quality-of-life add-on for Blender animators working with multiple armatures in one scene. Normally, jumping from one rig to another means: tab out to Object Mode → click the other rig → tab back into Pose Mode. Simply Switch collapses that into **one click on the mesh — or the bone — you want to pose.**

By BentBoneLab.

---

## Requirements
- **Blender 3.2 or newer** (uses the `temp_override` context API introduced in 3.2)

## Install
1. Download `SimplySwitch_v1.1.1.zip` (do **not** unzip it).
2. In Blender: **Edit → Preferences → Add-ons → Install from Disk…** (older versions: the **Install…** button).
3. Select the zip and enable **"Rigging: Simply Switch"** in the list.

## Use
1. Open the **N-panel** (press `N` in the 3D Viewport) and find the **Simply Switch** tab.
2. Click **Simply Switch** to start it (the button shows a pause icon while active).
3. Enter Pose Mode on any rig, then **click any rigged mesh — or any bone** — to jump to *its* armature, still in Pose Mode.
4. Clicking a **bone** also leaves that bone selected and active, so the click isn't spent on the switch.
5. **Double-click the rig you're currently posing** to drop back to Object Mode.
6. Click the button again to stop.

> **Note:** Simply Switch runs as a modal tool, so you click **Start once per Blender session.** Holding Shift/Ctrl/Alt or clicking empty space passes through normally, so it won't interfere with bone selection. Switching to Edit, Sculpt or Weight Paint mode leaves it running — it simply stands aside until you're back in Pose or Object Mode. Loading a file stops it (the panel updates to match).

## How it works
When active, it looks under your cursor in this order:

1. **A bone within 12px** — controls win over whatever is behind them, so an IK target overlapping another character still selects *its own* rig.
2. **A mesh** — finds the armature driving it, via Armature modifier or parent.
3. **A bone within 30px** — a looser grab for off-body controls (root circles, IK targets) that sit over empty space and would otherwise be unclickable.

It then switches to that armature, preserving your current pose. Linked characters and library overrides are resolved through the view layer, so overridden rigs activate correctly rather than failing against their linked originals.

## License
Simply Switch is free software under the **GNU General Public License v3.0 or later** (Blender add-ons that use the Blender Python API are derivative works and are licensed under the GPL). See the bundled `LICENSE` file. You are paying for the packaged tool, updates, and support — thank you for supporting independent tool development.

## Support
Questions or bugs: [your support email / link]

---
*BentBoneLab — tools that get out of the way so you can make the thing.*
