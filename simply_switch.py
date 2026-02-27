"""
Simply Switch — Blender Addon
Click on any rigged mesh while in Pose Mode to instantly switch to that rig.
No manual object-mode detour needed.
"""

bl_info = {
    "name": "Simply Switch",
    "author": "BlenDAZ",
    "version": (1, 0, 0),
    "blender": (3, 0, 0),
    "location": "3D Viewport (modal, runs automatically in Pose Mode)",
    "description": "Click a mesh to switch active armature without leaving Pose Mode",
    "category": "Rigging",
}

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_armature(mesh_obj):
    """Return the armature that drives mesh_obj, or None."""
    for mod in mesh_obj.modifiers:
        if mod.type == 'ARMATURE' and mod.object:
            return mod.object
    if mesh_obj.parent and mesh_obj.parent.type == 'ARMATURE':
        return mesh_obj.parent
    return None


def _raycast_scene(context, event):
    """Return (mesh_obj, armature) under the mouse, or (None, None)."""
    region = context.region
    rv3d  = context.space_data.region_3d if context.space_data else None
    if not region or not rv3d:
        return None, None

    coord       = (event.mouse_region_x, event.mouse_region_y)
    ray_origin  = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_vector  = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    ok, _loc, _nor, _idx, obj, _mat = context.scene.ray_cast(
        context.view_layer.depsgraph, ray_origin, ray_vector
    )

    if not ok or not obj or obj.type != 'MESH':
        return None, None

    armature = _find_armature(obj)
    return obj, armature


def _ops_override(context):
    """Build a context-override dict pointing at a non-camera VIEW_3D area."""
    best_area   = None
    best_region = None
    best_space  = None

    for area in context.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for sp in area.spaces:
            if sp.type != 'VIEW_3D':
                continue
            is_cam = sp.region_3d and sp.region_3d.view_perspective == 'CAMERA'
            for reg in area.regions:
                if reg.type == 'WINDOW':
                    if best_area is None or not is_cam:
                        best_area   = area
                        best_region = reg
                        best_space  = sp
                    if not is_cam:
                        break
        if best_area and best_space and best_space.region_3d and \
                best_space.region_3d.view_perspective != 'CAMERA':
            break

    if best_area and best_region:
        return {
            'area':       best_area,
            'region':     best_region,
            'space_data': best_space,
            'screen':     context.screen,
            'window':     context.window,
        }
    return None


def _switch_to_armature(context, armature):
    """Switch active object to armature and enter Pose Mode, preserving current pose."""
    ov = _ops_override(context)

    def mode_set(mode):
        if ov:
            with bpy.context.temp_override(**ov):
                bpy.ops.object.mode_set(mode=mode)
        else:
            bpy.ops.object.mode_set(mode=mode)

    # 1. Exit current mode (go to object mode on the old armature)
    if context.mode != 'OBJECT':
        mode_set('OBJECT')

    # 2. Deselect all, select and activate the new armature
    if ov:
        with bpy.context.temp_override(**ov):
            bpy.ops.object.select_all(action='DESELECT')
    else:
        bpy.ops.object.select_all(action='DESELECT')

    armature.select_set(True)
    context.view_layer.objects.active = armature

    # 3. Enter Pose Mode
    mode_set('POSE')

    print(f"[SimplySwitch] Switched to: {armature.name}")


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

    DOUBLE_CLICK_MS  = 0.3   # seconds
    DOUBLE_CLICK_PX  = 5     # pixel radius

    def modal(self, context, event):
        import time

        # Stop if we leave Pose Mode entirely (user manually exited)
        if context.mode not in ('POSE', 'OBJECT'):
            self._set_header(context, "")
            return {'CANCELLED'}

        # Stop if the operator was toggled off
        if not context.scene.simplyswitch_active:
            self._set_header(context, "")
            return {'CANCELLED'}

        # Only act on left-mouse press
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}

        # Ignore modifier keys (Shift, Ctrl, Alt — let Blender handle additive ops)
        if event.shift or event.ctrl or event.alt:
            return {'PASS_THROUGH'}

        # ---- Double-click detection ----------------------------------------
        now = time.time()
        pos = (event.mouse_region_x, event.mouse_region_y)

        is_double_click = False
        if self._last_click_time > 0:
            dt = now - self._last_click_time
            if dt < self.DOUBLE_CLICK_MS and self._last_click_pos:
                dx = pos[0] - self._last_click_pos[0]
                dy = pos[1] - self._last_click_pos[1]
                if (dx*dx + dy*dy) ** 0.5 < self.DOUBLE_CLICK_PX:
                    is_double_click = True

        self._last_click_time = now
        self._last_click_pos  = pos
        # --------------------------------------------------------------------

        # Raycast
        _mesh, armature = _raycast_scene(context, event)

        if armature is None:
            # Clicked on empty space or a non-rigged object — pass through
            return {'PASS_THROUGH'}

        on_active = (context.active_object == armature and context.mode == 'POSE')

        if is_double_click and on_active:
            # Double-click on the current rig → drop to Object Mode
            print(f"[SimplySwitch] Double-click — entering Object Mode on {armature.name}")
            ov = _ops_override(context)
            if ov:
                with bpy.context.temp_override(**ov):
                    bpy.ops.object.mode_set(mode='OBJECT')
            else:
                bpy.ops.object.mode_set(mode='OBJECT')
            # Reset so a triple-click doesn't re-trigger
            self._last_click_time = 0
            self._last_click_pos  = None
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}

        if on_active:
            # Single click on the rig we're already posing — don't interrupt bone selection
            return {'PASS_THROUGH'}

        # Switch to a different armature
        _switch_to_armature(context, armature)
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        self._last_click_time = 0.0
        self._last_click_pos  = None
        context.window_manager.modal_handler_add(self)
        self._set_header(context, "Simply Switch active — click any rig to switch")
        print("[SimplySwitch] Modal started")
        return {'RUNNING_MODAL'}

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
        context.scene.simplyswitch_active = True
        bpy.ops.simplyswitch.modal('INVOKE_DEFAULT')
        return {'FINISHED'}


class SIMPLYSWITCH_OT_stop(bpy.types.Operator):
    """Stop Simply Switch"""
    bl_idname = "simplyswitch.stop"
    bl_label  = "Stop Simply Switch"

    def execute(self, context):
        context.scene.simplyswitch_active = False
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
        active = getattr(context.scene, 'simplyswitch_active', False)

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

classes = (
    SIMPLYSWITCH_OT_modal,
    SIMPLYSWITCH_OT_start,
    SIMPLYSWITCH_OT_stop,
    SIMPLYSWITCH_PT_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.simplyswitch_active = bpy.props.BoolProperty(
        name="Simply Switch Active",
        default=False,
    )


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.simplyswitch_active


if __name__ == "__main__":
    register()
