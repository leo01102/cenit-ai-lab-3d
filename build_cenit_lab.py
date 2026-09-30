"""
CENIT AI Lab (Aula N°1) - Blender scene generator
=================================================
How to use: Blender -> Scripting tab -> Open (or paste) this file -> Run Script.
Works on Blender 3.6, 4.x and 5.x (EEVEE or Cycles).
WARNING: it wipes the current scene first, so every run starts clean.

Units: meters.  Origin: SW interior corner of the room, at floor level.
  X = room width (3.59 m), Y = room length (6.76 m), Z = height (2.97 m)
  Door  -> SOUTH wall (y = 0)
  Window + AC -> NORTH wall (y = L)
  Workstations -> EAST wall (x = W)
  Rack, wall switch cabinet, whiteboard -> WEST wall (x = 0)

Tip: to see the room from above, hide the "Techo" collection (eye icon).
"""
import math

import bpy
import bmesh
from mathutils import Vector

# ============================================================================
# 1. PARAMETERS
# ============================================================================
# --- Measured in the relevamiento (meters) ---
W, L, H = 3.59, 6.76, 2.97
WIN_W, WIN_H = 3.00, 1.10
DOOR_CLEARANCE_R = 0.90            # free radius required for the door swing

# --- Assumptions (not in the report: edit freely) ---
WALL_T = 0.15
WIN_SILL = 1.05                    # window sill height
DOOR_W, DOOR_H = 0.90, 2.10
DOOR_X0 = 2.45                     # x of the door opening's west edge (south wall)
DOOR_OPEN_DEG = 35                 # how open the door leaf is shown
CURTAIN_COVER = 0.70               # 0 = fully raised, 1 = fully closed

TABLE_LEN, TABLE_DEPTH, TABLE_H = 1.60, 0.70, 0.75
N_TABLES = 3
STATIONS_PER_TABLE = 2             # 3 tables x 2 = 6 workstations
TABLES_START_Y = 1.35
TABLES_GAP = 0.05

RACK_W, RACK_D, RACK_H = 0.60, 0.80, 2.00
RACK_Y0 = 0.30                     # rack sits on the west wall, starting at this y

CEIL_COLS, CEIL_ROWS = 6, 11       # ceiling tile grid
LED_COLS = (1, 4)                  # which tile columns get an LED panel
LED_ROWS = (1, 4, 7, 10)           # which tile rows get an LED panel
LED_POWER_W = 90

SHOW_LABELS = True                 # Spanish text labels
SHOW_DOOR_CLEARANCE = True         # red 90 cm door-swing decal on the floor

# ============================================================================
# 2. HELPERS
# ============================================================================
colls = {}
M = {}


def clear_scene():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in list(bpy.data.collections):
        bpy.data.collections.remove(coll)
    for block_group in (bpy.data.meshes, bpy.data.materials, bpy.data.curves,
                        bpy.data.lights, bpy.data.cameras):
        for block in list(block_group):
            block_group.remove(block)


def setup_collections():
    for name in ("Estructura", "Techo", "Mobiliario", "Equipamiento",
                 "Instalacion_electrica", "Rotulos", "Camaras_y_luces"):
        c = bpy.data.collections.new(name)
        bpy.context.scene.collection.children.link(c)
        colls[name] = c


def set_input(node, names, value):
    """Set the first existing input among `names` (handles 3.x / 4.x renames)."""
    for n in names:
        if n in node.inputs:
            node.inputs[n].default_value = value
            return True
    return False


def make_material(name, color, rough=0.6, metal=0.0, alpha=1.0,
                  emission=None, emission_strength=0.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    set_input(bsdf, ["Base Color"], (color[0], color[1], color[2], 1.0))
    set_input(bsdf, ["Roughness"], rough)
    set_input(bsdf, ["Metallic"], metal)
    if alpha < 1.0:
        set_input(bsdf, ["Alpha"], alpha)
        for attr, val in (("blend_method", 'BLEND'),
                          ("surface_render_method", 'BLENDED'),
                          ("shadow_method", 'NONE')):
            try:
                setattr(mat, attr, val)
            except Exception:
                pass
    if emission is not None:
        set_input(bsdf, ["Emission Color", "Emission"],
                  (emission[0], emission[1], emission[2], 1.0))
        set_input(bsdf, ["Emission Strength"], emission_strength)
    mat.diffuse_color = (color[0], color[1], color[2], alpha)
    return mat


def make_tile_material(name, c1, c2, grout, tile=0.40):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = nt.nodes.get("Principled BSDF")
    set_input(bsdf, ["Roughness"], 0.35)
    mat.diffuse_color = (c1[0], c1[1], c1[2], 1.0)
    try:
        coord = nt.nodes.new("ShaderNodeTexCoord")
        brick = nt.nodes.new("ShaderNodeTexBrick")
        brick.offset = 0.0
        brick.squash = 1.0
        brick.inputs["Color1"].default_value = (c1[0], c1[1], c1[2], 1.0)
        brick.inputs["Color2"].default_value = (c2[0], c2[1], c2[2], 1.0)
        brick.inputs["Mortar"].default_value = (grout[0], grout[1], grout[2], 1.0)
        brick.inputs["Scale"].default_value = 1.0
        brick.inputs["Mortar Size"].default_value = 0.008
        brick.inputs["Brick Width"].default_value = tile
        brick.inputs["Row Height"].default_value = tile
        nt.links.new(coord.outputs["Object"], brick.inputs["Vector"])
        nt.links.new(brick.outputs["Color"], bsdf.inputs["Base Color"])
    except Exception:
        set_input(bsdf, ["Base Color"], (c1[0], c1[1], c1[2], 1.0))
    return mat


def build_materials():
    M['paint'] = make_material("Paint_white", (0.92, 0.92, 0.90), 0.85)
    M['ceil'] = make_material("Ceiling_tile", (0.93, 0.93, 0.92), 0.9)
    M['tbar'] = make_material("Tbar", (0.85, 0.85, 0.86), 0.4, 0.5)
    M['led'] = make_material("LED_panel", (1, 1, 1), 0.3,
                             emission=(1.0, 0.98, 0.94), emission_strength=8.0)
    M['baseboard'] = make_material("Baseboard", (0.55, 0.36, 0.26), 0.7)
    M['tile'] = make_tile_material("Floor_tile", (0.78, 0.62, 0.50),
                                   (0.74, 0.58, 0.46), (0.55, 0.50, 0.45))
    M['wood'] = make_material("Desk_top_wood", (0.78, 0.58, 0.38), 0.5)
    M['desk_dark'] = make_material("Desk_dark", (0.04, 0.04, 0.045), 0.6)
    M['chair'] = make_material("Chair", (0.03, 0.03, 0.035), 0.6)
    M['bezel'] = make_material("Monitor_bezel", (0.01, 0.01, 0.012), 0.4)
    M['screen'] = make_material("Monitor_screen", (0.05, 0.10, 0.25), 0.2,
                                emission=(0.10, 0.25, 0.60), emission_strength=1.2)
    M['plastic'] = make_material("Plastic_black", (0.025, 0.025, 0.03), 0.5)
    M['tower'] = make_material("PC_tower", (0.03, 0.03, 0.04), 0.4, 0.3)
    M['rack'] = make_material("Rack_body", (0.80, 0.80, 0.72), 0.5, 0.3)
    M['rack_dark'] = make_material("Rack_dark", (0.05, 0.05, 0.06), 0.4, 0.8)
    M['bay'] = make_material("Server_bay", (0.09, 0.09, 0.10), 0.4, 0.6)
    M['glass_smoked'] = make_material("Glass_smoked", (0.02, 0.02, 0.03), 0.05, alpha=0.18)
    M['glass_window'] = make_material("Glass_window", (0.70, 0.85, 0.95), 0.02, alpha=0.12)
    M['alu'] = make_material("Aluminium", (0.85, 0.85, 0.87), 0.35, 0.8)
    M['curtain'] = make_material("Blackout_curtain", (0.03, 0.03, 0.05), 0.95)
    M['white_plastic'] = make_material("White_plastic", (0.95, 0.95, 0.95), 0.4)
    M['louver'] = make_material("AC_louver", (0.08, 0.08, 0.08), 0.5)
    M['led_green'] = make_material("LED_green", (0.1, 1.0, 0.2), 0.3,
                                   emission=(0.1, 1.0, 0.2), emission_strength=4.0)
    M['whiteboard'] = make_material("Whiteboard", (0.98, 0.98, 0.98), 0.15)
    M['door'] = make_material("Door_wood", (0.55, 0.38, 0.22), 0.6)
    M['cabinet'] = make_material("Wall_cabinet", (0.35, 0.36, 0.38), 0.5, 0.5)
    M['panel'] = make_material("Electrical_panel", (0.72, 0.74, 0.76), 0.5, 0.3)
    M['blue'] = make_material("Blue_plate", (0.05, 0.15, 0.55), 0.4)
    M['lbl_dark'] = make_material("Label_dark", (0.02, 0.02, 0.03), 0.5)
    M['lbl_light'] = make_material("Label_light", (0.95, 0.95, 0.95), 0.5)
    M['lbl_sign'] = make_material("Label_sign", (0.05, 0.15, 0.50), 0.5)
    M['clearance'] = make_material("Door_clearance", (1.0, 0.1, 0.1), 0.5, alpha=0.35,
                                   emission=(1.0, 0.1, 0.1), emission_strength=0.6)
    M['jack'] = make_material("Network_jack", (0.1, 0.25, 0.9), 0.4)


def box(name, p0, p1, mat, coll, loc=(0, 0, 0), rot=(0, 0, 0)):
    """Axis-aligned box from corner p0 to corner p1 (local coords)."""
    x0, x1 = sorted((p0[0], p1[0]))
    y0, y1 = sorted((p0[1], p1[1]))
    z0, z1 = sorted((p0[2], p1[2]))
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = x1 if v.co.x > 0 else x0
        v.co.y = y1 if v.co.y > 0 else y0
        v.co.z = z1 if v.co.z > 0 else z0
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = loc
    obj.rotation_euler = rot
    if mat is not None:
        mesh.materials.append(mat)
    coll.objects.link(obj)
    return obj


def cylinder(name, cx, cy, z0, z1, radius, mat, coll, seg=24):
    """Vertical cylinder."""
    bm = bmesh.new()
    bot = [bm.verts.new((cx + radius * math.cos(2 * math.pi * i / seg),
                         cy + radius * math.sin(2 * math.pi * i / seg), z0))
           for i in range(seg)]
    top = [bm.verts.new((cx + radius * math.cos(2 * math.pi * i / seg),
                         cy + radius * math.sin(2 * math.pi * i / seg), z1))
           for i in range(seg)]
    bm.faces.new(list(reversed(bot)))
    bm.faces.new(top)
    for i in range(seg):
        j = (i + 1) % seg
        bm.faces.new((bot[i], bot[j], top[j], top[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    if mat is not None:
        mesh.materials.append(mat)
    coll.objects.link(obj)
    return obj


# Text orientation presets (degrees). Text faces the given world direction.
FACE = {'+X': (90, 0, 90), '-X': (90, 0, -90), '-Y': (90, 0, 0), '+Y': (90, 0, 180)}


def label(text, loc, face, size, mat, extrude=0.002):
    if not SHOW_LABELS:
        return None
    curve = bpy.data.curves.new("txt", 'FONT')
    curve.body = text
    curve.size = size
    curve.align_x = 'CENTER'
    curve.align_y = 'CENTER'
    curve.extrude = extrude
    obj = bpy.data.objects.new("Label_" + text.replace("\n", " ")[:24], curve)
    obj.location = loc
    obj.rotation_euler = tuple(math.radians(a) for a in FACE[face])
    curve.materials.append(mat)
    colls['Rotulos'].objects.link(obj)
    return obj


# ============================================================================
# 3. ROOM SHELL
# ============================================================================
def build_shell():
    c = colls['Estructura']
    T = WALL_T
    top = H + 0.03

    # Floor slab (tile grid is aligned to the SW corner: object at world origin)
    box("Floor", (-T, -T, -0.10), (W + T, L + T, 0.0), M['tile'], c)

    # South wall (door opening)
    hx = DOOR_X0 + DOOR_W
    box("Wall_S_left", (-T, -T, 0), (DOOR_X0, 0, top), M['paint'], c)
    box("Wall_S_right", (hx, -T, 0), (W + T, 0, top), M['paint'], c)
    box("Wall_S_above_door", (DOOR_X0, -T, DOOR_H), (hx, 0, top), M['paint'], c)

    # North wall (window opening)
    wx0 = (W - WIN_W) / 2
    wx1 = wx0 + WIN_W
    wz1 = WIN_SILL + WIN_H
    box("Wall_N_left", (-T, L, 0), (wx0, L + T, top), M['paint'], c)
    box("Wall_N_right", (wx1, L, 0), (W + T, L + T, top), M['paint'], c)
    box("Wall_N_below_window", (wx0, L, 0), (wx1, L + T, WIN_SILL), M['paint'], c)
    box("Wall_N_above_window", (wx0, L, wz1), (wx1, L + T, top), M['paint'], c)

    # West and east walls
    box("Wall_W", (-T, -T, 0), (0, L + T, top), M['paint'], c)
    box("Wall_E", (W, -T, 0), (W + T, L + T, top), M['paint'], c)

    # Baseboards
    bh, bt = 0.08, 0.012
    box("Baseboard_W", (0, 0, 0), (bt, L, bh), M['baseboard'], c)
    box("Baseboard_E", (W - bt, 0, 0), (W, L, bh), M['baseboard'], c)
    box("Baseboard_N", (0, L - bt, 0), (W, L, bh), M['baseboard'], c)
    box("Baseboard_S_left", (0, 0, 0), (DOOR_X0, bt, bh), M['baseboard'], c)
    box("Baseboard_S_right", (hx, 0, 0), (W, bt, bh), M['baseboard'], c)


def build_window_and_curtain():
    c = colls['Estructura']
    wx0 = (W - WIN_W) / 2
    wx1 = wx0 + WIN_W
    wz0, wz1 = WIN_SILL, WIN_SILL + WIN_H
    f = 0.05
    y0, y1 = L + 0.04, L + 0.11
    xm = (wx0 + wx1) / 2
    box("Window_frame_bottom", (wx0, y0, wz0), (wx1, y1, wz0 + f), M['alu'], c)
    box("Window_frame_top", (wx0, y0, wz1 - f), (wx1, y1, wz1), M['alu'], c)
    box("Window_frame_left", (wx0, y0, wz0), (wx0 + f, y1, wz1), M['alu'], c)
    box("Window_frame_right", (wx1 - f, y0, wz0), (wx1, y1, wz1), M['alu'], c)
    box("Window_mullion", (xm - f / 2, y0, wz0), (xm + f / 2, y1, wz1), M['alu'], c)
    box("Window_glass", (wx0 + f, L + 0.07, wz0 + f), (wx1 - f, L + 0.08, wz1 - f),
        M['glass_window'], c)
    box("Window_sill", (wx0 - 0.03, L - 0.05, wz0 - 0.03), (wx1 + 0.03, L, wz0),
        M['white_plastic'], c)

    # Blackout curtain
    cx0, cx1 = wx0 - 0.12, wx1 + 0.12
    ctop = wz1 + 0.12
    cbottom_full = wz0 - 0.05
    cbottom = ctop - CURTAIN_COVER * (ctop - cbottom_full)
    box("Curtain_rod", (cx0, L - 0.11, ctop), (cx1, L - 0.05, ctop + 0.06), M['alu'], c)
    box("Curtain_fabric", (cx0, L - 0.09, cbottom), (cx1, L - 0.07, ctop), M['curtain'], c)


def build_door():
    c = colls['Estructura']
    hx = DOOR_X0 + DOOR_W
    fw = 0.045
    box("Door_frame_L", (DOOR_X0 - fw, 0, 0), (DOOR_X0, 0.03, DOOR_H + fw), M['white_plastic'], c)
    box("Door_frame_R", (hx, 0, 0), (hx + fw, 0.03, DOOR_H + fw), M['white_plastic'], c)
    box("Door_frame_T", (DOOR_X0, 0, DOOR_H), (hx, 0.03, DOOR_H + fw), M['white_plastic'], c)
    # Leaf: local origin at the hinge (east edge), opens INTO the room
    rot = (0, 0, -math.radians(DOOR_OPEN_DEG))
    box("Door_leaf", (-(DOOR_W - 0.01), 0.0, 0.01), (0, 0.04, DOOR_H - 0.005),
        M['door'], c, loc=(hx, 0, 0), rot=rot)
    box("Door_handle", (-(DOOR_W - 0.10), 0.04, 1.00), (-(DOOR_W - 0.24), 0.065, 1.03),
        M['alu'], c, loc=(hx, 0, 0), rot=rot)
    # Room plate outside the door
    box("Door_plate", (DOOR_X0 + 0.30, -WALL_T - 0.004, 1.60),
        (DOOR_X0 + 0.60, -WALL_T, 1.80), M['blue'], c)
    label("Aula N° 1", (DOOR_X0 + 0.45, -WALL_T - 0.006, 1.70), '-Y', 0.05, M['lbl_light'])

    if SHOW_DOOR_CLEARANCE:
        bm = bmesh.new()
        center = bm.verts.new((hx, 0.0, 0.004))
        steps = 24
        pts = []
        for i in range(steps + 1):
            a = math.radians(90 + 90 * i / steps)
            pts.append(bm.verts.new((hx + DOOR_CLEARANCE_R * math.cos(a),
                                     DOOR_CLEARANCE_R * math.sin(a), 0.004)))
        for i in range(steps):
            bm.faces.new((center, pts[i], pts[i + 1]))
        mesh = bpy.data.meshes.new("Door_clearance")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new("Door_clearance", mesh)
        mesh.materials.append(M['clearance'])
        c.objects.link(obj)


def build_ac():
    c = colls['Equipamiento']
    xc = 2.60
    z0, z1 = 2.40, 2.70
    box("AC_body", (xc - 0.43, L - 0.20, z0), (xc + 0.43, L, z1), M['white_plastic'], c)
    box("AC_louver", (xc - 0.36, L - 0.19, z0 - 0.005), (xc + 0.36, L - 0.06, z0 + 0.015), M['louver'], c)
    box("AC_led", (xc + 0.30, L - 0.203, 2.615), (xc + 0.315, L - 0.200, 2.63), M['led_green'], c)
    box("AC_pipe_h", (xc + 0.43, L - 0.06, 2.50), (3.51, L - 0.03, 2.53), M['white_plastic'], c)
    cylinder("AC_pipe_v", 3.50, L - 0.045, 1.40, 2.53, 0.016, M['white_plastic'], c, seg=12)


# ============================================================================
# 4. CEILING + LIGHTING
# ============================================================================
def build_ceiling():
    c = colls['Techo']
    tw, tl = W / CEIL_COLS, L / CEIL_ROWS
    box("Ceiling_slab", (0, 0, H), (W, L, H + 0.03), M['ceil'], c)
    for i in range(1, CEIL_COLS):
        x = i * tw
        box("Tbar_x%d" % i, (x - 0.013, 0, H - 0.012), (x + 0.013, L, H), M['tbar'], c)
    for j in range(1, CEIL_ROWS):
        y = j * tl
        box("Tbar_y%d" % j, (0, y - 0.013, H - 0.012), (W, y + 0.013, H), M['tbar'], c)

    n = 0
    for ci in LED_COLS:
        for rj in LED_ROWS:
            xa, xb = ci * tw + 0.02, (ci + 1) * tw - 0.02
            ya, yb = rj * tl + 0.02, (rj + 1) * tl - 0.02
            box("LED_panel_%d" % n, (xa, ya, H - 0.008), (xb, yb, H - 0.001), M['led'], c)
            light = bpy.data.lights.new("LED_light_%d" % n, 'AREA')
            light.shape = 'RECTANGLE'
            light.size = tw * 0.9
            light.size_y = tl * 0.9
            light.energy = LED_POWER_W
            light.color = (1.0, 0.98, 0.95)
            lobj = bpy.data.objects.new("LED_light_%d" % n, light)
            lobj.location = ((xa + xb) / 2, (ya + yb) / 2, H - 0.02)
            colls['Camaras_y_luces'].objects.link(lobj)
            n += 1


# ============================================================================
# 5. FURNITURE + WORKSTATIONS
# ============================================================================
def build_table(i, y0):
    c = colls['Mobiliario']
    y1 = y0 + TABLE_LEN
    x0 = W - TABLE_DEPTH
    box("Table%d_top" % i, (x0, y0, TABLE_H - 0.03), (W, y1, TABLE_H), M['wood'], c)
    box("Table%d_side_a" % i, (x0 + 0.03, y0, 0), (W - 0.03, y0 + 0.03, TABLE_H - 0.03), M['desk_dark'], c)
    box("Table%d_side_b" % i, (x0 + 0.03, y1 - 0.03, 0), (W - 0.03, y1, TABLE_H - 0.03), M['desk_dark'], c)
    box("Table%d_modesty" % i, (W - 0.12, y0 + 0.03, 0.30), (W - 0.09, y1 - 0.03, TABLE_H - 0.03),
        M['desk_dark'], c)


def build_workstation(idx, yc):
    c = colls['Equipamiento']
    z = TABLE_H
    # Monitor (faces -X, toward the user)
    box("WS%d_monitor_base" % idx, (W - 0.34, yc - 0.11, z), (W - 0.24, yc + 0.11, z + 0.012), M['bezel'], c)
    box("WS%d_monitor_neck" % idx, (W - 0.31, yc - 0.025, z), (W - 0.27, yc + 0.025, z + 0.16), M['bezel'], c)
    box("WS%d_monitor_body" % idx, (W - 0.30, yc - 0.30, z + 0.10), (W - 0.27, yc + 0.30, z + 0.44), M['bezel'], c)
    box("WS%d_monitor_screen" % idx, (W - 0.305, yc - 0.28, z + 0.12), (W - 0.30, yc + 0.28, z + 0.42), M['screen'], c)
    # Keyboard + mouse
    box("WS%d_keyboard" % idx, (W - 0.58, yc - 0.21, z), (W - 0.43, yc + 0.21, z + 0.02), M['plastic'], c)
    box("WS%d_mouse" % idx, (W - 0.56, yc + 0.27, z), (W - 0.50, yc + 0.32, z + 0.03), M['plastic'], c)
    # Tower under the table
    box("WS%d_tower" % idx, (W - 0.60, yc - 0.32, 0.0), (W - 0.15, yc - 0.12, 0.42), M['tower'], c)
    box("WS%d_tower_led" % idx, (W - 0.602, yc - 0.24, 0.32), (W - 0.60, yc - 0.22, 0.33), M['led_green'], c)


def build_chair(idx, cx, cy):
    c = colls['Mobiliario']
    cylinder("Chair%d_base" % idx, cx, cy, 0.04, 0.08, 0.28, M['chair'], c, seg=5)
    cylinder("Chair%d_column" % idx, cx, cy, 0.08, 0.44, 0.025, M['rack_dark'], c, seg=12)
    box("Chair%d_seat" % idx, (cx - 0.25, cy - 0.25, 0.44), (cx + 0.25, cy + 0.25, 0.50), M['chair'], c)
    box("Chair%d_back" % idx, (cx - 0.27, cy - 0.24, 0.50), (cx - 0.23, cy + 0.24, 0.95), M['chair'], c)


def build_workstations():
    idx = 0
    chair_x = W - TABLE_DEPTH - 0.35
    for i in range(N_TABLES):
        y0 = TABLES_START_Y + i * (TABLE_LEN + TABLES_GAP)
        build_table(i, y0)
        for k in range(STATIONS_PER_TABLE):
            yc = y0 + TABLE_LEN * (k + 0.5) / STATIONS_PER_TABLE
            build_workstation(idx, yc)
            build_chair(idx, chair_x, yc)
            idx += 1


def build_whiteboard():
    c = colls['Mobiliario']
    box("Whiteboard", (0, 3.30, 0.95), (0.02, 6.30, 1.95), M['whiteboard'], c)
    box("Whiteboard_tray", (0, 3.30, 0.93), (0.05, 6.30, 0.95), M['alu'], c)
    label("LABORATORIO DE INTELIGENCIA ARTIFICIAL\nCENIT", (0.006, 4.80, 2.30), '+X', 0.085, M['lbl_sign'])


# ============================================================================
# 6. RACK, SWITCH CABINET, ELECTRICAL
# ============================================================================
def build_rack():
    c = colls['Equipamiento']
    y0, y1 = RACK_Y0, RACK_Y0 + RACK_W
    yc = (y0 + y1) / 2
    t = 0.02
    box("Rack_side_S", (0, y0, 0), (RACK_D, y0 + t, RACK_H), M['rack'], c)
    box("Rack_side_N", (0, y1 - t, 0), (RACK_D, y1, RACK_H), M['rack'], c)
    box("Rack_back", (0, y0, 0), (t, y1, RACK_H), M['rack'], c)
    box("Rack_top", (0, y0, RACK_H - t), (RACK_D, y1, RACK_H), M['rack'], c)
    box("Rack_plinth", (0, y0, 0), (RACK_D, y1, 0.06), M['rack_dark'], c)

    # Front glass door
    fx0, fx1, b = RACK_D - 0.02, RACK_D, 0.04
    box("Rack_door_L", (fx0, y0, 0.06), (fx1, y0 + b, RACK_H - t), M['rack'], c)
    box("Rack_door_R", (fx0, y1 - b, 0.06), (fx1, y1, RACK_H - t), M['rack'], c)
    box("Rack_door_T", (fx0, y0, RACK_H - t - b), (fx1, y1, RACK_H - t), M['rack'], c)
    box("Rack_door_B", (fx0, y0, 0.06), (fx1, y1, 0.06 + b), M['rack'], c)
    box("Rack_door_glass", (fx0 + 0.008, y0 + b, 0.06 + b), (fx0 + 0.012, y1 - b, RACK_H - t - b),
        M['glass_smoked'], c)

    # Rails
    for ry in (y0 + 0.05, y1 - 0.05):
        for rx in (0.10, 0.72):
            box("Rack_rail", (rx - 0.01, ry - 0.01, 0.10), (rx + 0.01, ry + 0.01, RACK_H - 0.10),
                M['rack_dark'], c)

    # Top fans
    for fx in (0.25, 0.55):
        for fy in (yc - 0.12, yc + 0.12):
            cylinder("Rack_fan", fx, fy, RACK_H, RACK_H + 0.008, 0.05, M['rack_dark'], c, seg=16)

    def unit(name, z0, z1, depth, text, tsize, body_mat, led=True):
        x1 = 0.72
        x0 = x1 - depth
        ya, yb = yc - 0.24, yc + 0.24
        box(name, (x0, ya, z0), (x1, yb, z1), body_mat, c)
        box(name + "_bay", (x1, ya + 0.03, z0 + 0.01), (x1 + 0.003, yb - 0.10, z1 - 0.01), M['bay'], c)
        if led:
            box(name + "_led", (x1, yb - 0.06, (z0 + z1) / 2 - 0.006),
                (x1 + 0.003, yb - 0.04, (z0 + z1) / 2 + 0.006), M['led_green'], c)
        label(text, (x1 + 0.004, (ya + 0.03 + yb - 0.10) / 2, (z0 + z1) / 2), '+X', tsize, M['lbl_light'],
              extrude=0.001)

    unit("UPS", 0.12, 0.42, 0.60, "UPS", 0.07, M['plastic'])
    unit("Server1", 0.50, 0.678, 0.62, "SERVIDOR 1", 0.032, M['tower'])
    unit("Server2", 0.72, 0.898, 0.62, "SERVIDOR 2", 0.032, M['tower'])
    unit("Server3", 0.94, 1.118, 0.62, "SERVIDOR 3", 0.032, M['tower'])
    unit("PatchPanel", 1.30, 1.345, 0.05, "PATCH PANEL", 0.022, M['rack_dark'], led=False)

    label("RACK DE SERVIDORES", (0.006, yc, 2.30), '+X', 0.055, M['lbl_dark'])


def build_wall_switch_cabinet():
    c = colls['Equipamiento']
    y0, y1 = 1.25, 1.80
    z0, z1 = 1.40, 1.95
    d, t = 0.30, 0.015
    box("SwCab_back", (0, y0, z0), (t, y1, z1), M['cabinet'], c)
    box("SwCab_side_a", (0, y0, z0), (d, y0 + t, z1), M['cabinet'], c)
    box("SwCab_side_b", (0, y1 - t, z0), (d, y1, z1), M['cabinet'], c)
    box("SwCab_top", (0, y0, z1 - t), (d, y1, z1), M['cabinet'], c)
    box("SwCab_bottom", (0, y0, z0), (d, y1, z0 + t), M['cabinet'], c)
    box("SwCab_glass", (d, y0 + 0.02, z0 + 0.02), (d + 0.003, y1 - 0.02, z1 - 0.02), M['glass_smoked'], c)
    box("Switch", (0.05, y0 + 0.03, z1 - 0.12), (0.26, y1 - 0.03, z1 - 0.075), M['rack_dark'], c)
    box("Switch_ports", (0.26, y0 + 0.05, z1 - 0.105), (0.262, y1 - 0.05, z1 - 0.09), M['led_green'], c)
    label("SWITCH SECCIONAL", (d + 0.007, (y0 + y1) / 2, z0 + 0.13), '+X', 0.035, M['lbl_light'],
          extrude=0.001)


def build_electrical():
    c = colls['Instalacion_electrica']
    # Sectional electrical panel on the south wall
    box("Panel_body", (1.60, 0.0, 1.30), (2.00, 0.12, 1.85), M['panel'], c)
    box("Panel_door", (1.61, 0.12, 1.31), (1.99, 0.125, 1.84), M['panel'], c)
    label("TABLERO\nSECCIONAL", (1.80, 0.129, 1.62), '+Y', 0.045, M['lbl_dark'], extrude=0.001)

    # Surface raceways: panel -> above the door -> east corner -> along east wall
    box("Raceway_panel_up", (1.77, 0.0, 1.85), (1.83, 0.03, 2.25), M['white_plastic'], c)
    box("Raceway_over_door", (1.77, 0.0, 2.22), (W, 0.03, 2.28), M['white_plastic'], c)
    box("Raceway_corner_down", (W - 0.03, 0.02, 0.90), (W, 0.08, 2.28), M['white_plastic'], c)
    last_y = TABLES_START_Y + N_TABLES * (TABLE_LEN + TABLES_GAP)
    box("Raceway_east", (W - 0.03, 0.02, 0.90), (W, last_y, 0.96), M['white_plastic'], c)

    # Power outlet + network jack per workstation
    idx = 0
    for i in range(N_TABLES):
        y0 = TABLES_START_Y + i * (TABLE_LEN + TABLES_GAP)
        for k in range(STATIONS_PER_TABLE):
            yc = y0 + TABLE_LEN * (k + 0.5) / STATIONS_PER_TABLE
            box("Outlet_%d" % idx, (W - 0.055, yc - 0.05, 0.885), (W - 0.03, yc + 0.03, 0.965),
                M['white_plastic'], c)
            box("NetJack_%d" % idx, (W - 0.055, yc + 0.06, 0.90), (W - 0.03, yc + 0.11, 0.95),
                M['jack'], c)
            idx += 1


# ============================================================================
# 7. CAMERAS, WORLD, RENDER
# ============================================================================
def add_camera(name, loc, target, lens=18):
    data = bpy.data.cameras.new(name)
    data.lens = lens
    data.clip_start = 0.05
    data.clip_end = 100
    cam = bpy.data.objects.new(name, data)
    cam.location = loc
    direction = Vector(target) - Vector(loc)
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
    colls['Camaras_y_luces'].objects.link(cam)
    return cam


def build_cameras():
    cam_ws = add_camera("Cam_Workstations", (1.00, 0.15, 2.10), (3.00, 5.20, 0.90))
    add_camera("Cam_Rack", (W - 0.20, L - 0.25, 2.00), (0.40, 0.80, 1.00))
    add_camera("Cam_Door", (1.20, L - 0.35, 2.00), (2.40, 0.30, 1.20))
    plan = add_camera("Cam_Plan", (W / 2, L / 2, 9.0), (W / 2, L / 2, 0.0), lens=35)
    plan.data.type = 'ORTHO'
    plan.data.ortho_scale = 7.6
    plan.rotation_euler = (0, 0, 0)
    bpy.context.scene.camera = cam_ws


def setup_world_and_render():
    scene = bpy.context.scene
    scene.unit_settings.system = 'METRIC'
    scene.unit_settings.length_unit = 'METERS'

    world = bpy.data.worlds.get("World") or bpy.data.worlds.new("World")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.85, 0.87, 0.90, 1.0)
        bg.inputs[1].default_value = 0.6

    for engine in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            scene.render.engine = engine
            break
        except TypeError:
            continue
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    try:
        scene.view_settings.view_transform = 'Standard'
    except Exception:
        pass

    # Switch the 3D viewport to Material Preview + camera view (if a viewport is open)
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = 'MATERIAL'
                            space.region_3d.view_perspective = 'CAMERA'
                            space.clip_end = 100
    except Exception:
        pass


# ============================================================================
# 8. MAIN
# ============================================================================
def main():
    clear_scene()
    setup_collections()
    build_materials()
    build_shell()
    build_window_and_curtain()
    build_door()
    build_ac()
    build_ceiling()
    build_workstations()
    build_whiteboard()
    build_rack()
    build_wall_switch_cabinet()
    build_electrical()
    build_cameras()
    setup_world_and_render()
    print("CENIT AI Lab scene built: %d objects." % len(bpy.data.objects))


main()
