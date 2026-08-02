# SPDX-License-Identifier: GPL-3.0-or-later
# Simply Switch — Copyright (C) 2026 BentBoneLab
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. See the bundled LICENSE file for details.

"""
Simply Switch — Blender Addon
Click on any rigged mesh — or any bone — while in Pose Mode to instantly switch
to that rig. No manual object-mode detour needed. Clicking a bone also leaves
that bone selected and active, so the click isn't wasted on the switch.
"""

bl_info = {
    "name": "Simply Switch",
    "author": "BentBoneLab",
    "version": (1, 1, 1),
    "blender": (3, 2, 0),
    "location": "3D Viewport > Sidebar (N) > Simply Switch; runs modally in Pose Mode",
    "description": "Click a mesh to switch active armature without leaving Pose Mode",
    "doc_url": "",
    "tracker_url": "",
    "category": "Rigging",
}

import time
import traceback

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector


# Bone picking radii, in pixels. A control directly under the cursor beats the
# mesh behind it; the loose radius only applies when nothing else was hit.
BONE_PICK_TIGHT_PX = 12
BONE_PICK_LOOSE_PX = 30


# ---------------------------------------------------------------------------
# Runtime state
#
# Deliberately module-level rather than a Scene property: a Scene property is
# saved into the .blend and restored as True on load, while the modal handler
# is not — leaving the panel claiming Simply Switch is live when it is dead.
# ---------------------------------------------------------------------------

_running = False

# Bumped on every start. A handler whose generation is stale cancels itself, so
# a quick stop->start can't leave two handlers processing the same click.
_generation = 0


def is_running():
    return _running


def _set_running(value):
    global _running
    _running = value
    _tag_redraw_all()


def _next_generation():
    global _generation
    _generation += 1
    return _generation


def _tag_redraw_all():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        if not win.screen:
            continue
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve(context, obj):
    """Map a datablock to the object the view layer can actually activate.

    A linked character and its local library override share a name, and the
    mesh modifiers of the linked copy point at the linked armature. Selecting
    or activating that one raises RuntimeError ("not in View Layer"), so always
    go back through the view layer, which hands out the local override.
    """
    if obj is None:
        return None
    obj = obj.original
    return context.view_layer.objects.get(obj.name)


def _find_armature(mesh_obj):
    """Return the armature that drives mesh_obj, or None."""
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    if mesh_obj.parent and mesh_obj.parent.type == 'ARMATURE':
        return mesh_obj.parent
    return None


def _bone_is_visible(arm_obj, bone):
    """True if bone is actually drawn in the viewport."""
    if bone.hide:
        return False
    collections = getattr(bone, "collections", None)
    if collections is not None:          # Blender 4.0+
        return not collections or any(bc.is_visible for bc in collections)
    layers = getattr(bone, "layers", None)   # Blender 3.x
    if layers is not None:
        return any(b and a for b, a in zip(layers, arm_obj.data.layers))
    return True


def _project(pv, region, co):
    """Project a local-space point through pv into region pixel coords."""
    v = pv @ co.to_4d()
    if v.w <= 0.0:                       # behind the viewer
        return None
    return Vector((region.width * 0.5 * (1.0 + v.x / v.w),
                   region.height * 0.5 * (1.0 + v.y / v.w)))


def _dist_to_segment_2d(p, a, b):
    ab = b - a
    denom = ab.length_squared
    if denom == 0.0:
        return (p - a).length
    t = max(0.0, min(1.0, (p - a).dot(ab) / denom))
    return (p - (a + ab * t)).length


def _pick_bone(context, region, rv3d, coord, radius):
    """Return (armature, bone_name) for the nearest visible bone within radius.

    Meshes alone are not enough: IK targets, root circles and other off-body
    controls sit over empty space, and those are exactly what you click to grab
    a character.
    """
    click = Vector(coord)
    best_obj = None
    best_bone = None
    best_dist = float(radius)

    for obj in context.view_layer.objects:
        if obj.type != 'ARMATURE' or not obj.visible_get() or obj.hide_select:
            continue
        pv = rv3d.perspective_matrix @ obj.matrix_world
        for pbone in obj.pose.bones:
            if not _bone_is_visible(obj, pbone.bone):
                continue
            head = _project(pv, region, pbone.head)
            tail = _project(pv, region, pbone.tail)
            if head is None or tail is None:
                continue
            dist = _dist_to_segment_2d(click, head, tail)
            if dist < best_dist:
                best_dist = dist
                best_obj = obj
                best_bone = pbone.name

    return best_obj, best_bone


# Bone selection lives on PoseBone from Blender 5.0 on, and on Bone before that.
# Writing the pose side also matters for linked rigs: an overridden object has a
# writable pose even when its armature data is still library data.
_POSEBONE_HAS_SELECT = "select" in bpy.types.PoseBone.bl_rna.properties


def _bone_select_get(pbone):
    return pbone.select if _POSEBONE_HAS_SELECT else pbone.bone.select


def _bone_select_set(pbone, value):
    if _POSEBONE_HAS_SELECT:
        pbone.select = value
    else:
        pbone.bone.select = value


def _activate_bone(armature, bone_name):
    """Select bone_name on armature and make it active, clearing other bones."""
    pbone = armature.pose.bones.get(bone_name)
    if pbone is None:
        return False
    try:
        for other in armature.pose.bones:
            if _bone_select_get(other):
                _bone_select_set(other, False)
        _bone_select_set(pbone, True)
        armature.data.bones.active = pbone.bone
    except (RuntimeError, AttributeError) as exc:
        print(f"[SimplySwitch] Could not activate bone {bone_name}: {exc}")
        return False
    return True


def _raycast_mesh(context, region, rv3d, coord):
    """Return the mesh under coord, or None."""
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_vector = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    ok, _loc, _nor, _idx, obj, _mat = context.scene.ray_cast(
        context.view_layer.depsgraph, ray_origin, ray_vector
    )
    if not ok or not obj or obj.type != 'MESH':
        return None
    return obj


def _pick_target(context, event):
    """Return (armature, bone_name) under the mouse. Both may be None.

    bone_name is set only when the user actually clicked a bone, so that the
    switch can carry the click through and leave that bone active.
    """
    region = context.region
    rv3d = context.space_data.region_3d if context.space_data else None

    # Only the main viewport region has a usable 2D->3D mapping; a click in the
    # sidebar or toolbar would otherwise build a ray from bogus coordinates.
    if not region or region.type != 'WINDOW' or not rv3d:
        return None, None

    coord = (event.mouse_region_x, event.mouse_region_y)

    # A control right under the cursor wins over whatever mesh is behind it —
    # the user is aiming at the bone, even if it overlaps another character.
    armature, bone_name = _pick_bone(context, region, rv3d, coord,
                                     BONE_PICK_TIGHT_PX)
    if armature is None:
        mesh = _raycast_mesh(context, region, rv3d, coord)
        if mesh is not None:
            armature = _find_armature(mesh)

    if armature is None:
        armature, bone_name = _pick_bone(context, region, rv3d, coord,
                                         BONE_PICK_LOOSE_PX)

    return _resolve(context, armature), bone_name


def _ops_override(context):
    """Build a context-override dict pointing at a non-camera VIEW_3D area."""
    areas = []
    if context.area and context.area.type == 'VIEW_3D':
        areas.append(context.area)
    areas.extend(a for a in context.screen.areas
                 if a.type == 'VIEW_3D' and a not in areas)

    fallback = None
    for area in areas:
        space = next((s for s in area.spaces if s.type == 'VIEW_3D'), None)
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if not space or not region:
            continue
        override = {
            'area':       area,
            'region':     region,
            'space_data': space,
            'screen':     context.screen,
            'window':     context.window,
        }
        is_cam = space.region_3d and space.region_3d.view_perspective == 'CAMERA'
        if not is_cam:
            return override
        fallback = fallback or override

    return fallback


def _heal_pose_tool(context):
    """Clear an invalid brush tool left in Pose mode's tool slot.

    Blender's brush-asset system (4.3+) can leave a sculpt/paint brush id such
    as 'builtin_brush.Draw' remembered as the active tool for Pose mode, where
    no brush tool is valid. Every entry into Pose Mode then reports
    "Tool 'builtin_brush.Draw' not found for space 'VIEW_3D'". Switching rigs
    enters Pose Mode, so the switch surfaces it on every click.

    Only touches the slot when it holds a brush tool, so a deliberate tool
    choice is never overridden, and only while actually in Pose Mode (that is
    the slot bpy.ops.wm.tool_set_by_id writes to).
    """
    if context.mode != 'POSE':
        return

    tool = next((t for t in context.workspace.tools
                 if t.space_type == 'VIEW_3D' and t.mode == 'POSE'), None)
    if tool is None or 'brush' not in tool.idname.lower():
        return

    ov = _ops_override(context)
    try:
        if ov:
            with bpy.context.temp_override(**ov):
                bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
        else:
            bpy.ops.wm.tool_set_by_id(name='builtin.select_box')
    except RuntimeError as exc:
        print(f"[SimplySwitch] Could not reset Pose tool: {exc}")


def _switch_to_armature(context, armature):
    """Switch active object to armature and enter Pose Mode, preserving pose.

    Returns True on success. Never raises — a raising modal is torn down by
    Blender, which would silently kill the addon mid-session.
    """
    if not armature.visible_get() or armature.hide_select:
        return False

    ov = _ops_override(context)

    def mode_set(mode):
        if ov:
            with bpy.context.temp_override(**ov):
                bpy.ops.object.mode_set(mode=mode)
        else:
            bpy.ops.object.mode_set(mode=mode)

    def select_none():
        if ov:
            with bpy.context.temp_override(**ov):
                bpy.ops.object.select_all(action='DESELECT')
        else:
            bpy.ops.object.select_all(action='DESELECT')

    try:
        # 1. Exit current mode (go to object mode on the old armature)
        if context.mode != 'OBJECT':
            mode_set('OBJECT')

        # 2. Deselect all, select and activate the new armature
        select_none()
        armature.select_set(True)
        context.view_layer.objects.active = armature

        # 3. Enter Pose Mode
        mode_set('POSE')
    except (RuntimeError, ReferenceError) as exc:
        print(f"[SimplySwitch] Could not switch to {armature.name}: {exc}")
        return False

    # Correct a stale brush tool in the Pose slot so this switch (and every
    # future entry into Pose Mode) doesn't report a missing tool.
    _heal_pose_tool(context)

    print(f"[SimplySwitch] Switched to: {armature.name}")
    return True


# ---------------------------------------------------------------------------
# Modal operator
# ---------------------------------------------------------------------------

class SIMPLYSWITCH_OT_modal(bpy.types.Operator):
    """Simply Switch modal — click any rigged mesh to switch to its armature"""
    bl_idname  = "simplyswitch.modal"
    bl_label   = "Simply Switch Modal"
    bl_options = {'REGISTER'}

    # Double-click detection state
    _last_click_time = 0.0
    _last_click_pos  = None
    _generation      = 0

    DOUBLE_CLICK_MS  = 0.3   # seconds
    DOUBLE_CLICK_PX  = 5     # pixel radius

    def modal(self, context, event):
        # Blender removes a modal handler that raises, and the user would get
        # no feedback beyond Simply Switch quietly never working again.
        try:
            return self._modal(context, event)
        except Exception:
            traceback.print_exc()
            return {'PASS_THROUGH'}

    def _modal(self, context, event):
        # Stop if the operator was toggled off, or superseded by a newer start
        if not _running or self._generation != _generation:
            self._finish(context)
            return {'CANCELLED'}

        # Edit / Sculpt / Weight Paint are none of our business, but they are no
        # reason to die either — the user comes back to Pose Mode afterwards.
        if context.mode not in ('POSE', 'OBJECT'):
            return {'PASS_THROUGH'}

        # Only act on left-mouse press
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        # Ignore modifier keys (Shift, Ctrl, Alt — let Blender handle additive ops)
        if event.shift or event.ctrl or event.alt:
            return {'PASS_THROUGH'}

        armature, bone_name = _pick_target(context, event)
        if armature is None:
            # Clicked on empty space or a non-rigged object — pass through
            return {'PASS_THROUGH'}

        # ---- Double-click detection ----------------------------------------
        now = time.time()
        pos = (event.mouse_region_x, event.mouse_region_y)

        is_double_click = False
        if self._last_click_time > 0 and self._last_click_pos:
            dt = now - self._last_click_time
            if dt < self.DOUBLE_CLICK_MS:
                dx = pos[0] - self._last_click_pos[0]
                dy = pos[1] - self._last_click_pos[1]
                if (dx*dx + dy*dy) ** 0.5 < self.DOUBLE_CLICK_PX:
                    is_double_click = True

        self._last_click_time = now
        self._last_click_pos  = pos
        # --------------------------------------------------------------------

        on_active = (context.active_object == armature and context.mode == 'POSE')

        if is_double_click and on_active:
            # Double-click on the current rig → drop to Object Mode
            print(f"[SimplySwitch] Double-click — entering Object Mode on {armature.name}")
            ov = _ops_override(context)
            try:
                if ov:
                    with bpy.context.temp_override(**ov):
                        bpy.ops.object.mode_set(mode='OBJECT')
                else:
                    bpy.ops.object.mode_set(mode='OBJECT')
            except RuntimeError as exc:
                print(f"[SimplySwitch] Could not enter Object Mode: {exc}")
                return {'PASS_THROUGH'}
            # Reset so a triple-click doesn't re-trigger
            self._reset_click_state()
            self._tag_redraw(context)
            return {'RUNNING_MODAL'}

        if on_active:
            # Single click on the rig we're already posing — don't interrupt bone selection
            return {'PASS_THROUGH'}

        # Switch to a different armature
        if not _switch_to_armature(context, armature):
            return {'PASS_THROUGH'}

        # If the click landed on a bone, carry it through: the user aimed at
        # that control, so leave it selected and active rather than making them
        # click a second time.
        if bone_name:
            _activate_bone(armature, bone_name)

        # The switch consumed this click, so the follow-up click must not read
        # as a double-click and drop them into Object Mode.
        self._reset_click_state()
        self._tag_redraw(context)
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        self._reset_click_state()
        self._generation = _next_generation()
        context.window_manager.modal_handler_add(self)
        _set_running(True)
        # Heal up front so even the first switch is quiet (Start is normally
        # pressed while already in Pose Mode).
        _heal_pose_tool(context)
        self._set_header(context, "Simply Switch active — click any rig to switch")
        print("[SimplySwitch] Modal started")
        return {'RUNNING_MODAL'}

    def _reset_click_state(self):
        self._last_click_time = 0.0
        self._last_click_pos  = None

    def _finish(self, context):
        self._set_header(context, "")
        print("[SimplySwitch] Modal stopped")

    def _tag_redraw(self, context):
        if context.area:
            context.area.tag_redraw()

    def _set_header(self, context, text):
        try:
            if context.area:
                context.area.header_text_set(text or None)
        except Exception:
            pass


class SIMPLYSWITCH_OT_start(bpy.types.Operator):
    """Start Simply Switch"""
    bl_idname = "simplyswitch.start"
    bl_label  = "Start Simply Switch"

    def execute(self, context):
        if _running:
            return {'CANCELLED'}
        bpy.ops.simplyswitch.modal('INVOKE_DEFAULT')
        return {'FINISHED'}


class SIMPLYSWITCH_OT_stop(bpy.types.Operator):
    """Stop Simply Switch"""
    bl_idname = "simplyswitch.stop"
    bl_label  = "Stop Simply Switch"

    def execute(self, context):
        _set_running(False)
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class SIMPLYSWITCH_PT_panel(bpy.types.Panel):
    bl_label      = "Simply Switch"
    bl_idname     = "SIMPLYSWITCH_PT_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category   = "Simply Switch"

    def draw(self, context):
        layout = self.layout
        active = is_running()

        row = layout.row()
        row.scale_y = 1.5
        if active:
            row.operator("simplyswitch.stop",  text="Simply Switch", icon='PAUSE',    depress=True)
        else:
            row.operator("simplyswitch.start", text="Simply Switch", icon='POSE_HLT')

        if active:
            layout.label(text="Click any rig to switch", icon='INFO')


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@bpy.app.handlers.persistent
def _on_load(_dummy):
    # Loading a file tears down every modal handler; keep the panel honest.
    _set_running(False)


classes = (
    SIMPLYSWITCH_OT_modal,
    SIMPLYSWITCH_OT_start,
    SIMPLYSWITCH_OT_stop,
    SIMPLYSWITCH_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)


def unregister():
    global _running
    _running = False
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
