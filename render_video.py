"""
render_video.py - CENIT AI Lab (Aula N° 1) Cinematic Walkthrough & Presentation
================================================================================
Builds upon build_cenit_lab.py (without modifying it), setting up:
- Cinematic camera choreography across 6 3D shots (bezier easing, DoF, modest focal lengths).
- Ken Burns photo sequence for Shot 1 ("ESTADO ACTUAL") from relevamiento photos.
- 3D billboard labels with Track-To / camera-alignment, dark semi-transparent plates,
  leader lines, emissive markers, and strict color coding (EXISTENTE, A REACONDICIONAR, PROPUESTO).
- Animated infrastructure power path glow & traveling light pulse.
- Automatic ceiling hiding (Techo hide_render) exclusively during Shot 7 (Top-Down Plan).
- Top-down architectural plan view with full corner legend, titles, and zone callouts.
- Headless EEVEE rendering and ffmpeg H.264 video assembly.

Usage:
  blender -b -P render_video.py -- --test-stills   # Renders 1 still per shot to renders/test/
  blender -b -P render_video.py -- --preview       # Renders 1280x720 preview video
  blender -b -P render_video.py -- --final         # Renders 1920x1080 final video
"""

import math
import os
import runpy
import subprocess
import sys
import time

import bmesh
import bpy
from mathutils import Vector, Matrix

# ============================================================================
# 1. LOAD BASE SCENE & CONFIGURE DIRECTORIES
# ============================================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BUILD_SCRIPT = os.path.join(BASE_DIR, "build_cenit_lab.py")
PHOTOS_DIR = os.path.join(BASE_DIR, "photos")
RENDERS_DIR = os.path.join(BASE_DIR, "renders")

print(f"[*] Executing base scene generator: {BUILD_SCRIPT}...")
runpy.run_path(BUILD_SCRIPT)

scene = bpy.context.scene

def calibrate_lighting_and_exposure():
    """
    Calibrates lighting and color management to avoid blown-out whites.
    Uses AgX filmic transform, realistic exposure, and balanced light intensities.
    """
    # 1. AgX color management for natural highlight roll-off
    try:
        scene.view_settings.view_transform = 'AgX'
        scene.view_settings.look = 'AgX - Base Contrast'
    except Exception:
        pass
    scene.view_settings.exposure = -1.15

    # 2. Configure EEVEE Next shadows to avoid buffer full warnings and maximize performance
    try:
        scene.eevee.shadow_pool_size = '2048'
        scene.eevee.shadow_resolution_scale = 0.75
    except Exception:
        pass

    # 3. Tone down world background strength so it doesn't flood interior
    world = scene.world
    if world and world.node_tree:
        bg = world.node_tree.nodes.get("Background")
        if bg:
            bg.inputs[1].default_value = 0.05

    # 3. Tone down LED panel mesh emission so it doesn't wash out ceiling
    led_mat = bpy.data.materials.get("LED_panel")
    if led_mat and led_mat.node_tree:
        bsdf = led_mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Emission Strength" in bsdf.inputs:
            bsdf.inputs["Emission Strength"].default_value = 1.8

    # 4. Balanced area lights
    for obj in bpy.data.objects:
        if obj.type == 'LIGHT' and obj.name.startswith("LED_light"):
            obj.data.energy = 42.0

    # 5. Distinct contrast for electrical panel and text against white walls
    panel_mat = bpy.data.materials.get("Electrical_panel")
    if panel_mat and panel_mat.node_tree:
        bsdf = panel_mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (0.52, 0.55, 0.58, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.65

    lbl_dark_mat = bpy.data.materials.get("Label_dark")
    if lbl_dark_mat and lbl_dark_mat.node_tree:
        bsdf = lbl_dark_mat.node_tree.nodes.get("Principled BSDF")
        if bsdf and "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (0.01, 0.01, 0.01, 1.0)

# ============================================================================
# 2. CONFIGURATION & CONSTANTS
# ============================================================================
# Room dimensions from relevamiento
W, L, H = 3.59, 6.76, 2.97

# Timeline parameters (24 fps, 60 seconds total = 1440 frames)
FPS = 24
TOTAL_FRAMES = 1440
scene.render.fps = FPS
scene.frame_start = 1
scene.frame_end = TOTAL_FRAMES

# Shot frame ranges
SHOT_RANGES = {
    1: (1, 144),       # 0 - 6 s:  Estado actual (Photos)
    2: (145, 384),     # 6 - 16 s: Entrance Walkthrough
    3: (385, 624),     # 16 - 26 s: Rack Zone
    4: (625, 864),     # 26 - 36 s: Workstations
    5: (865, 1152),    # 36 - 48 s: Infrastructure & Power Path
    6: (1153, 1296),   # 48 - 54 s: Climate & Access Clearance
    7: (1297, 1440),   # 54 - 60 s: Top-down Plan with Legend
}

# Color palette for labels
COLOR_EXISTENTE = (0.95, 0.48, 0.05, 1.0)        # Orange
COLOR_REACONDICIONAR = (0.15, 0.58, 0.95, 1.0)   # Blue
COLOR_PROPUESTO = (0.10, 0.85, 0.35, 1.0)        # Green
COLOR_DARK_BG = (0.05, 0.06, 0.08, 1.0)         # Solid dark plate (noise-free)
COLOR_WHITE = (1.0, 1.0, 1.0, 1.0)
COLOR_CYAN_PULSE = (0.15, 0.85, 1.0, 1.0)

TAG_COLORS = {
    'EXISTENTE': COLOR_EXISTENTE,
    'A REACONDICIONAR': COLOR_REACONDICIONAR,
    'PROPUESTO': COLOR_PROPUESTO,
}

# Ensure collections exist
def get_or_create_collection(name):
    c = bpy.data.collections.get(name)
    if not c:
        c = bpy.data.collections.new(name)
        scene.collection.children.link(c)
    return c

coll_anim = get_or_create_collection("Animacion_y_HUD")
coll_cams = get_or_create_collection("Camaras_Shots")

# ============================================================================
# 3. HELPER FUNCTIONS: ANIMATION, EASING & MATERIALS
# ============================================================================
def set_bezier_interpolation(obj):
    """Sets all fcurves of an object to BEZIER easing."""
    if obj.animation_data and obj.animation_data.action:
        # Blender 5.x compatibility for action fcurves
        try:
            fcurves = obj.animation_data.action.fcurves
        except AttributeError:
            fcurves = []
        for fc in fcurves:
            for kp in fc.keyframe_points:
                kp.interpolation = 'BEZIER'
                kp.easing = 'EASE_IN_OUT'


def make_emissive_mat(name, color, strength=3.0, alpha=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], alpha)
        bsdf.inputs["Emission Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Emission Strength"].default_value = strength
        if alpha < 1.0:
            bsdf.inputs["Alpha"].default_value = alpha
    return mat


def make_plate_mat(name, color=COLOR_DARK_BG):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get("Principled BSDF")
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
        bsdf.inputs["Roughness"].default_value = 0.5
        bsdf.inputs["Alpha"].default_value = 1.0
    return mat


MAT_EXISTENTE = make_emissive_mat("Mat_Existente", COLOR_EXISTENTE, strength=4.0)
MAT_REACONDICIONAR = make_emissive_mat("Mat_Reacondicionar", COLOR_REACONDICIONAR, strength=4.0)
MAT_PROPUESTO = make_emissive_mat("Mat_Propuesto", COLOR_PROPUESTO, strength=4.0)
MAT_PLATE = make_plate_mat("Mat_Label_Plate", COLOR_DARK_BG)
MAT_WHITE_TXT = make_emissive_mat("Mat_White_Text", COLOR_WHITE, strength=2.5)
MAT_LINE = make_emissive_mat("Mat_Leader_Line", (0.9, 0.9, 0.9, 1.0), strength=2.0)
MAT_PULSE = make_emissive_mat("Mat_Infra_Pulse", COLOR_CYAN_PULSE, strength=8.0)

TAG_MATS = {
    'EXISTENTE': MAT_EXISTENTE,
    'A REACONDICIONAR': MAT_REACONDICIONAR,
    'PROPUESTO': MAT_PROPUESTO,
}

# ============================================================================
# 4. CAMERA CHOREOGRAPHY
# ============================================================================
created_cameras = {}

def create_shot_camera(name, lens=26, clip_start=0.05, clip_end=50.0):
    cam_data = bpy.data.cameras.new(name)
    cam_data.lens = lens
    cam_data.clip_start = clip_start
    cam_data.clip_end = clip_end
    cam_obj = bpy.data.objects.new(name, cam_data)
    coll_cams.objects.link(cam_obj)
    created_cameras[name] = cam_obj
    return cam_obj


def look_at(eye, target):
    """
    Computes zero-roll Euler angles (XYZ) pointing a Blender camera (-Z forward, +Y up)
    from eye position towards target position, ensuring camera horizon is strictly level.
    """
    forward = (target - eye).normalized()
    right = forward.cross(Vector((0, 0, 1)))
    if right.length < 1e-5:
        right = Vector((1, 0, 0))
    else:
        right.normalize()
    up = right.cross(forward).normalized()
    m = Matrix((
        [right.x, up.x, -forward.x, eye.x],
        [right.y, up.y, -forward.y, eye.y],
        [right.z, up.z, -forward.z, eye.z],
        [0, 0, 0, 1]
    ))
    return m.to_euler()


def set_cam_keyframe(cam, frame, loc, targ, prev_euler=None):
    cam.location = loc
    e = look_at(Vector(loc), targ)
    if prev_euler is not None:
        e.make_compatible(prev_euler)
    cam.rotation_euler = e
    cam.keyframe_insert("location", frame=frame)
    cam.keyframe_insert("rotation_euler", frame=frame)
    return e


def setup_cameras():
    # ------------------------------------------------------------------------
    # Shot 2: Entrance Walkthrough (frames 145 - 384)
    # Starts inside door, slow smooth dolly forward showing the whole room
    # ------------------------------------------------------------------------
    cam2 = create_shot_camera("Cam_Shot2_Entrance", lens=24)
    e2 = set_cam_keyframe(cam2, 145, (2.55, 0.45, 1.65), Vector((1.90, 4.30, 1.25)))
    set_cam_keyframe(cam2, 384, (2.15, 2.10, 1.60), Vector((1.80, 5.20, 1.20)), prev_euler=e2)
    set_bezier_interpolation(cam2)

    # ------------------------------------------------------------------------
    # Shot 3: Rack Zone (frames 385 - 624)
    # Balanced framing of the rack, servers, UPS, switch cabinet, and ventilation
    # ------------------------------------------------------------------------
    cam3 = create_shot_camera("Cam_Shot3_Rack", lens=26)
    cam3.data.dof.use_dof = True
    cam3.data.dof.focus_distance = 1.80
    cam3.data.dof.aperture_fstop = 4.0

    e3 = set_cam_keyframe(cam3, 385, (2.15, 0.45, 1.45), Vector((0.40, 0.70, 1.15)))
    set_cam_keyframe(cam3, 624, (1.80, 1.75, 1.55), Vector((0.30, 1.25, 1.35)), prev_euler=e3)
    set_bezier_interpolation(cam3)

    # ------------------------------------------------------------------------
    # Shot 4: Workstations (frames 625 - 864)
    # Slow slide along the aisle + push-in with shallow DoF focused on workstations
    # ------------------------------------------------------------------------
    cam4 = create_shot_camera("Cam_Shot4_Workstations", lens=28)
    cam4.data.dof.use_dof = True
    cam4.data.dof.focus_distance = 1.45
    cam4.data.dof.aperture_fstop = 2.8

    e4 = set_cam_keyframe(cam4, 625, (1.80, 1.50, 1.25), Vector((3.15, 2.45, 0.95)))
    set_cam_keyframe(cam4, 864, (2.00, 4.40, 1.20), Vector((3.20, 5.30, 0.95)), prev_euler=e4)
    set_bezier_interpolation(cam4)

    # ------------------------------------------------------------------------
    # Shot 5: Infrastructure (frames 865 - 1152)
    # Follows the electrical path: Tablero -> over door -> along east wall -> jacks
    # ------------------------------------------------------------------------
    cam5 = create_shot_camera("Cam_Shot5_Infrastructure", lens=24)
    # Point 1: Facing Tablero on south wall
    e5_1 = set_cam_keyframe(cam5, 865, (1.75, 1.40, 1.55), Vector((1.75, 0.05, 1.55)))
    # Point 2: Pan & track up toward raceway over door
    e5_2 = set_cam_keyframe(cam5, 955, (2.20, 1.60, 1.80), Vector((2.80, 0.05, 2.25)), prev_euler=e5_1)
    # Point 3: View down the east wall raceway, outlets and jacks
    e5_3 = set_cam_keyframe(cam5, 1045, (2.10, 2.20, 1.55), Vector((3.50, 3.20, 0.92)), prev_euler=e5_2)
    # Point 4: Glide along east wall showing outlets and network jacks
    set_cam_keyframe(cam5, 1152, (2.10, 4.20, 1.55), Vector((3.50, 5.00, 0.92)), prev_euler=e5_3)
    set_bezier_interpolation(cam5)

    # ------------------------------------------------------------------------
    # Shot 6: Climate and Access Clearance (frames 1153 - 1296)
    # AC unit, blackout curtain, and the red door swing clearance zone
    # ------------------------------------------------------------------------
    cam6 = create_shot_camera("Cam_Shot6_ClimateAccess", lens=24)
    # Beat 1: High framing of AC and Blackout curtain on North wall
    e6_1 = set_cam_keyframe(cam6, 1153, (2.00, 4.20, 2.05), Vector((2.40, 6.76, 2.35)))
    e6_2 = set_cam_keyframe(cam6, 1210, (1.90, 3.80, 2.00), Vector((2.40, 6.76, 2.30)), prev_euler=e6_1)
    # Beat 2: Frame the entrance door and red door-swing clearance on floor
    e6_3 = set_cam_keyframe(cam6, 1225, (1.90, 2.40, 1.75), Vector((2.85, 0.45, 0.05)), prev_euler=e6_2)
    set_cam_keyframe(cam6, 1296, (1.80, 2.00, 1.65), Vector((2.85, 0.45, 0.05)), prev_euler=e6_3)
    set_bezier_interpolation(cam6)

    # ------------------------------------------------------------------------
    # Shot 7: Top-down Plan View (frames 1297 - 1440)
    # Orthographic top-down view, ceiling hidden, rotated 90 deg so length fits horizontally
    # ------------------------------------------------------------------------
    cam7 = create_shot_camera("Cam_Shot7_PlanView", lens=35)
    cam7.data.type = 'ORTHO'
    cam7.data.ortho_scale = 8.5
    cam7.location = (W / 2, L / 2, 9.0)
    # Rotate 90 degrees around Z so the 6.76m length runs horizontally across 16:9 frame
    cam7.rotation_euler = (0, 0, math.radians(90))
    cam7.keyframe_insert("location", frame=1297)
    cam7.keyframe_insert("rotation_euler", frame=1297)
    cam7.keyframe_insert("location", frame=1440)
    cam7.keyframe_insert("rotation_euler", frame=1440)

    # Bind cameras to timeline markers
    markers = [
        (SHOT_RANGES[2][0], "Marker_Shot2", cam2),
        (SHOT_RANGES[3][0], "Marker_Shot3", cam3),
        (SHOT_RANGES[4][0], "Marker_Shot4", cam4),
        (SHOT_RANGES[5][0], "Marker_Shot5", cam5),
        (SHOT_RANGES[6][0], "Marker_Shot6", cam6),
        (SHOT_RANGES[7][0], "Marker_Shot7", cam7),
    ]
    scene.timeline_markers.clear()
    for frame, mname, cam in markers:
        m = scene.timeline_markers.new(mname, frame=frame)
        m.camera = cam


# ============================================================================
# 5. LABELS & TOOLTIPS SYSTEM
# ============================================================================
def create_3d_label(name, anchor_pos, offset_pos, tag_type, title, desc,
                    start_frame, end_frame, target_cam):
    """
    Creates a 3D label with marker pin, leader line, dark background plate,
    color tag bar, and 2-line emissive text that faces the active camera.
    """
    tag_color = TAG_COLORS.get(tag_type, COLOR_PROPUESTO)
    tag_mat = TAG_MATS.get(tag_type, MAT_PROPUESTO)

    # Root empty
    root = bpy.data.objects.new(f"{name}_Root", None)
    root.location = anchor_pos
    coll_anim.objects.link(root)

    # Marker sphere at anchor
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.022)
    m_mesh = bpy.data.meshes.new(f"{name}_MarkerMesh")
    bm.to_mesh(m_mesh)
    bm.free()
    marker = bpy.data.objects.new(f"{name}_Marker", m_mesh)
    marker.parent = root
    m_mesh.materials.append(tag_mat)
    coll_anim.objects.link(marker)

    # Target position for the card
    target_pos = Vector(offset_pos)

    # Leader line curve
    curve_data = bpy.data.curves.new(f"{name}_LineCurve", 'CURVE')
    curve_data.dimensions = '3D'
    curve_data.bevel_depth = 0.003
    spline = curve_data.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (0, 0, 0, 1)
    spline.points[1].co = (target_pos.x, target_pos.y, target_pos.z, 1)
    line_obj = bpy.data.objects.new(f"{name}_Line", curve_data)
    line_obj.parent = root
    curve_data.materials.append(MAT_LINE)
    coll_anim.objects.link(line_obj)

    # Card container at target_pos
    card = bpy.data.objects.new(f"{name}_Card", None)
    card.location = target_pos
    card.parent = root
    # Copy rotation of the target camera so the label is parallel to the camera film
    cr = card.constraints.new('COPY_ROTATION')
    cr.target = target_cam
    coll_anim.objects.link(card)

    # Background plate mesh (sleek modern proportions, centered on card)
    pw, ph = 0.68, 0.22
    bm_p = bmesh.new()
    v1 = bm_p.verts.new((-pw / 2, -ph / 2, 0.0))
    v2 = bm_p.verts.new(( pw / 2, -ph / 2, 0.0))
    v3 = bm_p.verts.new(( pw / 2,  ph / 2, 0.0))
    v4 = bm_p.verts.new((-pw / 2,  ph / 2, 0.0))
    bm_p.faces.new((v1, v2, v3, v4))
    bmesh.ops.recalc_face_normals(bm_p, faces=bm_p.faces)
    p_mesh = bpy.data.meshes.new(f"{name}_PlateMesh")
    bm_p.to_mesh(p_mesh)
    bm_p.free()
    plate = bpy.data.objects.new(f"{name}_Plate", p_mesh)
    plate.parent = card
    p_mesh.materials.append(MAT_PLATE)
    coll_anim.objects.link(plate)

    # Color tag bar at top of plate
    bm_t = bmesh.new()
    th = 0.036
    tv1 = bm_t.verts.new((-pw / 2, ph / 2 - th, 0.001))
    tv2 = bm_t.verts.new(( pw / 2, ph / 2 - th, 0.001))
    tv3 = bm_t.verts.new(( pw / 2, ph / 2,      0.001))
    tv4 = bm_t.verts.new((-pw / 2, ph / 2,      0.001))
    bm_t.faces.new((tv1, tv2, tv3, tv4))
    bmesh.ops.recalc_face_normals(bm_t, faces=bm_t.faces)
    t_mesh = bpy.data.meshes.new(f"{name}_TagBarMesh")
    bm_t.to_mesh(t_mesh)
    bm_t.free()
    tag_bar = bpy.data.objects.new(f"{name}_TagBar", t_mesh)
    tag_bar.parent = card
    t_mesh.materials.append(tag_mat)
    coll_anim.objects.link(tag_bar)

    # Tag text
    c_tag = bpy.data.curves.new(f"{name}_TagTxt", 'FONT')
    c_tag.body = tag_type
    c_tag.size = 0.022
    c_tag.align_x = 'LEFT'
    c_tag.align_y = 'CENTER'
    tag_txt = bpy.data.objects.new(f"{name}_TagTxt", c_tag)
    tag_txt.parent = card
    tag_txt.location = (-pw / 2 + 0.022, ph / 2 - th / 2, 0.003)
    c_tag.materials.append(MAT_WHITE_TXT)
    coll_anim.objects.link(tag_txt)

    # Title text (large and bold)
    c_title = bpy.data.curves.new(f"{name}_TitleTxt", 'FONT')
    c_title.body = title
    c_title.size = 0.044
    c_title.align_x = 'LEFT'
    c_title.align_y = 'CENTER'
    title_obj = bpy.data.objects.new(f"{name}_TitleTxt", c_title)
    title_obj.parent = card
    title_obj.location = (-pw / 2 + 0.022, 0.020, 0.003)
    c_title.materials.append(MAT_WHITE_TXT)
    coll_anim.objects.link(title_obj)

    # Description text (clear and prominent)
    c_desc = bpy.data.curves.new(f"{name}_DescTxt", 'FONT')
    c_desc.body = desc
    c_desc.size = 0.024
    c_desc.align_x = 'LEFT'
    c_desc.align_y = 'CENTER'
    desc_obj = bpy.data.objects.new(f"{name}_DescTxt", c_desc)
    desc_obj.parent = card
    desc_obj.location = (-pw / 2 + 0.022, -0.045, 0.003)
    c_desc.materials.append(MAT_WHITE_TXT)
    coll_anim.objects.link(desc_obj)

    # Animate scale for entry and exit
    fade_frames = 12
    root.scale = (0, 0, 0)
    root.keyframe_insert("scale", frame=max(1, start_frame - 1))
    root.scale = (1, 1, 1)
    root.keyframe_insert("scale", frame=start_frame + fade_frames)
    root.keyframe_insert("scale", frame=end_frame - fade_frames)
    root.scale = (0, 0, 0)
    root.keyframe_insert("scale", frame=end_frame)
    set_bezier_interpolation(root)

    return root


def setup_labels():
    # ------------------------------------------------------------------------
    # Shot 2 Labels: Entrance Walkthrough
    # ------------------------------------------------------------------------
    create_3d_label(
        "LBL_Entr_Lab", (1.80, 3.50, 1.20), (0.05, -0.20, 0.35),
        'PROPUESTO', "Laboratorio de IA", "Propuesta de modernización CENIT",
        SHOT_RANGES[2][0] + 10, SHOT_RANGES[2][1] - 10, created_cameras["Cam_Shot2_Entrance"]
    )

    # ------------------------------------------------------------------------
    # Shot 3 Labels: Rack Zone (Sequenced cleanly in 3 beats)
    # ------------------------------------------------------------------------
    cam3 = created_cameras["Cam_Shot3_Rack"]
    # Beat 1: Rack 42U overview
    create_3d_label(
        "LBL_RCK", (0.65, 0.90, 1.45), (0.25, 0.15, 0.05),
        'EXISTENTE', "Rack de Servidores", "Gabinete 42U con ventilación",
        SHOT_RANGES[3][0] + 10, SHOT_RANGES[3][0] + 80, cam3
    )
    # Beat 2: Hardware upgrades - Servidores and UPS
    create_3d_label(
        "LBL_SRV23", (0.65, 0.70, 0.95), (0.25, 0.12, 0.15),
        'A REACONDICIONAR', "Servidores 2 y 3", "Mantenimiento y upgrade de hardware",
        SHOT_RANGES[3][0] + 85, SHOT_RANGES[3][0] + 160, cam3
    )
    create_3d_label(
        "LBL_UPS", (0.65, 0.70, 0.28), (0.25, 0.12, 0.10),
        'PROPUESTO', "UPS", "Respaldo eléctrico ininterrumpido",
        SHOT_RANGES[3][0] + 95, SHOT_RANGES[3][0] + 160, cam3
    )
    # Beat 3: Switch cabinet on wall
    create_3d_label(
        "LBL_SWCAB", (0.30, 1.55, 1.75), (0.25, -0.05, 0.10),
        'PROPUESTO', "Switch Seccional", "Gabinete de comunicaciones en pared",
        SHOT_RANGES[3][0] + 165, SHOT_RANGES[3][1] - 8, cam3
    )

    # ------------------------------------------------------------------------
    # Shot 4 Labels: Workstations (Sequenced in 3 distinct beats)
    # ------------------------------------------------------------------------
    cam4 = created_cameras["Cam_Shot4_Workstations"]
    # Beat 1: Workstations overview
    create_3d_label(
        "LBL_WS", (3.20, 2.50, 1.15), (-0.35, -0.15, 0.05),
        'PROPUESTO', "Puestos de Trabajo", "6 estaciones de investigación",
        SHOT_RANGES[4][0] + 10, SHOT_RANGES[4][0] + 80, cam4
    )
    # Beat 2: Ergonomic chairs
    create_3d_label(
        "LBL_CHAIR", (2.55, 3.40, 0.85), (0.00, 0.00, 0.35),
        'PROPUESTO', "Sillas Ergonómicas", "Confort para jornadas intensivas",
        SHOT_RANGES[4][0] + 85, SHOT_RANGES[4][0] + 160, cam4
    )
    # Beat 3: Desks layout
    create_3d_label(
        "LBL_DESK", (3.10, 5.00, 0.85), (-0.25, -0.10, 0.25),
        'EXISTENTE', "Mesas de Trabajo", "Distribución lineal optimizada",
        SHOT_RANGES[4][0] + 165, SHOT_RANGES[4][1] - 8, cam4
    )

    # ------------------------------------------------------------------------
    # Shot 5 Labels: Infrastructure (Sequenced cleanly along the electrical path)
    # ------------------------------------------------------------------------
    cam5 = created_cameras["Cam_Shot5_Infrastructure"]
    create_3d_label(
        "LBL_PANEL", (1.80, 0.12, 1.60), (-0.10, 0.35, 0.05),
        'PROPUESTO', "Tablero Seccional", "Protección termomagnética y diferencial",
        SHOT_RANGES[5][0] + 12, SHOT_RANGES[5][0] + 95, cam5
    )
    create_3d_label(
        "LBL_RACEWAY", (3.50, 0.15, 2.25), (-0.25, 0.25, -0.20),
        'PROPUESTO', "Canaletas Perimetrales", "Separación de fuerza motriz y datos",
        SHOT_RANGES[5][0] + 100, SHOT_RANGES[5][0] + 185, cam5
    )
    create_3d_label(
        "LBL_OUTLETS", (3.50, 3.20, 1.05), (-0.35, -0.10, 0.18),
        'PROPUESTO', "Tomas y Red RJ45", "Alimentación y red dedicada por puesto",
        SHOT_RANGES[5][0] + 190, SHOT_RANGES[5][1] - 10, cam5
    )

    # ------------------------------------------------------------------------
    # Shot 6 Labels: Climate and Access
    # ------------------------------------------------------------------------
    cam6 = created_cameras["Cam_Shot6_ClimateAccess"]
    # Beat 1: AC and curtain
    create_3d_label(
        "LBL_AC", (2.60, 6.55, 2.55), (-0.30, -0.20, -0.20),
        'EXISTENTE', "Aire Acondicionado", "Climatización para equipamiento y usuarios",
        SHOT_RANGES[6][0] + 10, SHOT_RANGES[6][0] + 65, cam6
    )
    create_3d_label(
        "LBL_CURTAIN", (1.40, 6.65, 1.80), (0.20, -0.20, 0.10),
        'PROPUESTO', "Cortina Blackout", "Aislación térmica y control de brillo",
        SHOT_RANGES[6][0] + 10, SHOT_RANGES[6][0] + 65, cam6
    )
    # Beat 2: Door swing clearance
    create_3d_label(
        "LBL_CLEARANCE", (2.85, 0.45, 0.05), (-0.45, 0.50, 0.55),
        'PROPUESTO', "Área de Apertura", "Radio de 90 cm despejado según norma",
        SHOT_RANGES[6][0] + 72, SHOT_RANGES[6][1] - 8, cam6
    )


# ============================================================================
# 6. INFRASTRUCTURE MOVING LIGHT PULSE ANIMATION
# ============================================================================
def setup_infrastructure_pulse():
    """
    Animates a glowing energy pulse along the electrical raceway:
    Tablero -> above door -> east corner -> down to raceway -> along east wall.
    """
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=3, radius=0.035)
    pulse_mesh = bpy.data.meshes.new("Infra_PulseMesh")
    bm.to_mesh(pulse_mesh)
    bm.free()
    pulse_obj = bpy.data.objects.new("Infra_Pulse", pulse_mesh)
    pulse_mesh.materials.append(MAT_PULSE)
    coll_anim.objects.link(pulse_obj)

    # Accompanying point light for soft local illumination
    p_light = bpy.data.lights.new("Infra_PulseLight", 'POINT')
    p_light.energy = 25.0
    p_light.color = (0.2, 0.85, 1.0)
    p_light.shadow_soft_size = 0.05
    p_light_obj = bpy.data.objects.new("Infra_PulseLight", p_light)
    p_light_obj.parent = pulse_obj
    coll_anim.objects.link(p_light_obj)

    # Route waypoints (South wall -> door header -> east wall -> workstations)
    waypoints = [
        (SHOT_RANGES[5][0],        (1.80, 0.06, 1.55)),  # Tablero seccional
        (SHOT_RANGES[5][0] + 45,   (1.80, 0.06, 2.25)),  # Up to door header
        (SHOT_RANGES[5][0] + 110,  (3.55, 0.06, 2.25)),  # Corner junction over door
        (SHOT_RANGES[5][0] + 155,  (3.55, 0.06, 0.93)),  # Down to table raceway height
        (SHOT_RANGES[5][0] + 215,  (3.55, 2.80, 0.93)),  # Midway through desks
        (SHOT_RANGES[5][1],        (3.55, 6.30, 0.93)),  # End of workstations
    ]

    for frame, loc in waypoints:
        pulse_obj.location = loc
        pulse_obj.keyframe_insert("location", frame=frame)
    set_bezier_interpolation(pulse_obj)

    # Hide pulse outside Shot 5
    pulse_obj.scale = (0, 0, 0)
    pulse_obj.keyframe_insert("scale", frame=1)
    pulse_obj.keyframe_insert("scale", frame=SHOT_RANGES[5][0] - 1)
    pulse_obj.scale = (1, 1, 1)
    pulse_obj.keyframe_insert("scale", frame=SHOT_RANGES[5][0])
    pulse_obj.keyframe_insert("scale", frame=SHOT_RANGES[5][1])
    pulse_obj.scale = (0, 0, 0)
    pulse_obj.keyframe_insert("scale", frame=SHOT_RANGES[5][1] + 1)
    set_bezier_interpolation(pulse_obj)


# ============================================================================
# 7. CEILING VISIBILITY ANIMATION (SHOT 7 ONLY)
# ============================================================================
def setup_ceiling_animation():
    """
    Ensures the 'Techo' collection and all its children are hidden
    ONLY during Shot 7 (Top-down Plan View), and visible in all other shots.
    """
    techo_coll = bpy.data.collections.get("Techo")
    techo_objects = list(techo_coll.objects) if techo_coll else []

    # Keyframe hide_render on all objects in Techo
    for obj in techo_objects:
        obj.hide_render = False
        obj.keyframe_insert("hide_render", frame=1)
        obj.keyframe_insert("hide_render", frame=SHOT_RANGES[7][0] - 1)
        obj.hide_render = True
        obj.keyframe_insert("hide_render", frame=SHOT_RANGES[7][0])
        obj.keyframe_insert("hide_render", frame=SHOT_RANGES[7][1])

    # Also register frame handler for live interactive preview
    def on_frame(sc):
        c = bpy.data.collections.get("Techo")
        if c:
            c.hide_render = (sc.frame_current >= SHOT_RANGES[7][0])
    bpy.app.handlers.frame_change_pre.clear()
    bpy.app.handlers.frame_change_pre.append(on_frame)


# ============================================================================
# 8. SHOT 7: TOP-DOWN PLAN VIEW OVERLAY & LEGEND
# ============================================================================
def setup_shot7_plan_overlay():
    """
    Adds corner legend, top title, and room zone markers to Shot 7.
    """
    cam7 = created_cameras["Cam_Shot7_PlanView"]

    root_hud = bpy.data.objects.new("Shot7_HUD_Root", None)
    # Position just below camera, oriented flat to the orthographic plan
    root_hud.location = (W / 2, L / 2, 8.0)
    root_hud.rotation_euler = (0, 0, math.radians(90))
    coll_anim.objects.link(root_hud)

    # Top title banner
    c_title = bpy.data.curves.new("Plan_MainTitle", 'FONT')
    c_title.body = "DISTRIBUCIÓN Y EQUIPAMIENTO - AULA N° 1"
    c_title.size = 0.22
    c_title.align_x = 'CENTER'
    c_title.align_y = 'CENTER'
    t_obj = bpy.data.objects.new("Plan_MainTitle", c_title)
    t_obj.parent = root_hud
    # In cam7 orientation: X is along room width, Y is along room length
    t_obj.location = (0.0, 2.15, 0.0)
    c_title.materials.append(MAT_WHITE_TXT)
    coll_anim.objects.link(t_obj)

    c_sub = bpy.data.curves.new("Plan_SubTitle", 'FONT')
    c_sub.body = "Laboratorio de Inteligencia Artificial - CENIT / UNLaR"
    c_sub.size = 0.13
    c_sub.align_x = 'CENTER'
    c_sub.align_y = 'CENTER'
    s_obj = bpy.data.objects.new("Plan_SubTitle", c_sub)
    s_obj.parent = root_hud
    s_obj.location = (0.0, 1.95, 0.0)
    c_sub.materials.append(MAT_WHITE_TXT)
    coll_anim.objects.link(s_obj)

    # Corner Legend Card (Placed cleanly in open floor area)
    # Width 3.6m, height 0.65m in ortho world units
    lw, lh = 3.60, 0.65
    bm_l = bmesh.new()
    lv1 = bm_l.verts.new((-lw / 2, -lh / 2, -0.01))
    lv2 = bm_l.verts.new(( lw / 2, -lh / 2, -0.01))
    lv3 = bm_l.verts.new(( lw / 2,  lh / 2, -0.01))
    lv4 = bm_l.verts.new((-lw / 2,  lh / 2, -0.01))
    bm_l.faces.new((lv1, lv2, lv3, lv4))
    bmesh.ops.recalc_face_normals(bm_l, faces=bm_l.faces)
    l_mesh = bpy.data.meshes.new("Plan_LegendPlate")
    bm_l.to_mesh(l_mesh)
    bm_l.free()
    l_plate = bpy.data.objects.new("Plan_LegendPlate", l_mesh)
    l_plate.parent = root_hud
    l_plate.location = (1.20, 0.40, 0.0)
    l_mesh.materials.append(MAT_PLATE)
    coll_anim.objects.link(l_plate)

    legend_items = [
        ("EXISTENTE", "Rack, AC, Ventana, Puerta, Mesas, Pizarra", MAT_EXISTENTE, 0.20),
        ("A REACONDICIONAR", "Servidores 2 y 3 (Upgrade y Mantenimiento)", MAT_REACONDICIONAR, 0.0),
        ("PROPUESTO", "UPS, Tablero, Switch, Canaletas, Puestos (x6), Sillas", MAT_PROPUESTO, -0.20),
    ]

    for tag, desc, mat, y_off in legend_items:
        # Color chip
        bm_c = bmesh.new()
        cw, ch = 0.12, 0.12
        cv1 = bm_c.verts.new((-cw / 2, -ch / 2, 0.001))
        cv2 = bm_c.verts.new(( cw / 2, -ch / 2, 0.001))
        cv3 = bm_c.verts.new(( cw / 2,  ch / 2, 0.001))
        cv4 = bm_c.verts.new((-cw / 2,  ch / 2, 0.001))
        bm_c.faces.new((cv1, cv2, cv3, cv4))
        bmesh.ops.recalc_face_normals(bm_c, faces=bm_c.faces)
        c_mesh = bpy.data.meshes.new(f"Chip_{tag}")
        bm_c.to_mesh(c_mesh)
        bm_c.free()
        chip = bpy.data.objects.new(f"Chip_{tag}", c_mesh)
        chip.parent = l_plate
        chip.location = (-lw / 2 + 0.18, y_off, 0.002)
        c_mesh.materials.append(mat)
        coll_anim.objects.link(chip)

        # Legend text
        c_txt = bpy.data.curves.new(f"Txt_{tag}", 'FONT')
        c_txt.body = f"[{tag}]  {desc}"
        c_txt.size = 0.095
        c_txt.align_x = 'LEFT'
        c_txt.align_y = 'CENTER'
        txt_obj = bpy.data.objects.new(f"Txt_{tag}", c_txt)
        txt_obj.parent = l_plate
        txt_obj.location = (-lw / 2 + 0.32, y_off, 0.002)
        c_txt.materials.append(MAT_WHITE_TXT)
        coll_anim.objects.link(txt_obj)

    # Animate visibility: only visible during Shot 7
    root_hud.scale = (0, 0, 0)
    root_hud.keyframe_insert("scale", frame=1)
    root_hud.keyframe_insert("scale", frame=SHOT_RANGES[7][0] - 1)
    root_hud.scale = (1, 1, 1)
    root_hud.keyframe_insert("scale", frame=SHOT_RANGES[7][0])
    root_hud.keyframe_insert("scale", frame=SHOT_RANGES[7][1])
    set_bezier_interpolation(root_hud)


# ============================================================================
# 9. COMPOSITING SETUP (VIGNETTE & GLOW)
# ============================================================================
def setup_compositor():
    try:
        if hasattr(scene, "node_tree") and scene.node_tree:
            scene.use_nodes = True
            tree = scene.node_tree
            tree.nodes.clear()
            rlayers = tree.nodes.new('CompositorNodeRLayers')
            comp = tree.nodes.new('CompositorNodeComposite')
            tree.links.new(rlayers.outputs['Image'], comp.inputs['Image'])
    except Exception as e:
        print(f"[!] Compositor note: {e}")


# ============================================================================
# 10. SHOT 1 KEN BURNS PHOTO SEQUENCE (via ffmpeg)
# ============================================================================
def generate_shot1_photos(out_video_path, width=1280, height=720):
    """
    Creates the 6-second Ken Burns photo sequence from photos/
    with lower-third title: 'Estado actual del Aula N° 1'.
    """
    photos_dir = PHOTOS_DIR
    if not os.path.exists(photos_dir):
        print(f"[-] photos/ folder does not exist at {photos_dir}, skipping Shot 1.")
        return None

    # Pick 4 prime photos
    candidate_photos = [
        "Screenshot 2026-09-28 210156.png",  # Wide room entrance view
        "Screenshot 2026-09-28 210216.png",  # Server rack & cable status
        "Screenshot 2026-09-28 210045.png",  # Server chassis & Aula 1 sign
        "Screenshot 2026-09-28 210241.png",  # North wall / window / whiteboard
    ]
    valid_photos = [os.path.join(photos_dir, p) for p in candidate_photos if os.path.exists(os.path.join(photos_dir, p))]
    if not valid_photos:
        print("[-] No valid photos found in photos/.")
        return None

    print(f"[*] Generating Shot 1 Ken Burns photo video from {len(valid_photos)} photos...")
    os.makedirs(os.path.dirname(os.path.abspath(out_video_path)), exist_ok=True)

    # Generate Ken Burns clip per photo (each ~1.65s, total 6.0s with crossfades)
    temp_clips = []
    font_path = "C\\:/Windows/Fonts/segoeui.ttf"
    title_text = "Estado actual del Aula N\\u00B0 1"

    test_dir = os.path.join(RENDERS_DIR, "test")
    os.makedirs(test_dir, exist_ok=True)

    for idx, p in enumerate(valid_photos):
        clip_path = os.path.join(test_dir, f"photo_clip_{idx}.mp4")
        temp_clips.append(clip_path)
        # Ken Burns zoom + lower-third banner
        cmd = [
            "C:\\ffmpeg\\bin\\ffmpeg.exe", "-y",
            "-loop", "1", "-t", "1.65",
            "-i", p,
            "-vf", (
                f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,"
                f"crop={width*2}:{height*2},"
                f"zoompan=z='min(zoom+0.0012,1.15)':d=40:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={width}x{height}:fps=24,"
                f"drawtext=text='{title_text}':fontfile='{font_path}':fontsize=28:fontcolor=white:"
                f"box=1:boxcolor=black@0.75:boxborderw=12:x=50:y=h-75"
            ),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            clip_path
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    # Concat clips with brief crossfade
    concat_list_file = os.path.join(test_dir, "photo_clips.txt")
    with open(concat_list_file, "w") as f:
        for c in temp_clips:
            f.write(f"file '{c.replace(chr(92), '/')}'\n")

    cmd_concat = [
        "C:\\ffmpeg\\bin\\ffmpeg.exe", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_list_file,
        "-t", "6.0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        out_video_path
    ]
    subprocess.run(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    print(f"[+] Shot 1 photo sequence saved: {out_video_path}")
    return out_video_path


# ============================================================================
# 11. RENDER & VIDEO ASSEMBLY PIPELINE
# ============================================================================
def render_test_stills(width=1280, height=720):
    """Renders 1 still from each shot to visually inspect framing and labels."""
    print("[*] Rendering 1 test still per shot into renders/test/...")
    out_dir = os.path.join(RENDERS_DIR, "test")
    os.makedirs(out_dir, exist_ok=True)

    test_frames = {
        "Shot2_Entrance": (SHOT_RANGES[2][0] + 120, "Cam_Shot2_Entrance"),
        "Shot3_Rack": (SHOT_RANGES[3][0] + 120, "Cam_Shot3_Rack"),
        "Shot4_Workstations": (SHOT_RANGES[4][0] + 45, "Cam_Shot4_Workstations"),
        "Shot5_Infrastructure": (SHOT_RANGES[5][0] + 45, "Cam_Shot5_Infrastructure"),
        "Shot6_ClimateAccess": (SHOT_RANGES[6][0] + 85, "Cam_Shot6_ClimateAccess"),
        "Shot7_PlanView": (SHOT_RANGES[7][0] + 70, "Cam_Shot7_PlanView"),
    }

    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.use_raytracing = False
    scene.eevee.taa_render_samples = 4
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100

    for shot_name, (fr, cam_name) in test_frames.items():
        scene.frame_set(fr)
        cam = created_cameras.get(cam_name)
        if cam:
            scene.camera = cam
        out_path = os.path.join(out_dir, f"{shot_name}.png")
        scene.render.filepath = out_path
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        print(f"  [+] {shot_name} (frame {fr}) rendered in {time.time()-t0:.2f}s -> {out_path}")


def render_and_assemble(mode="preview"):
    """
    Renders the 3D frames and assembles the full walkthrough video.
    mode: 'preview' (1280x720, fast) or 'final' (1920x1080, high quality)
    """
    is_final = (mode == "final")
    width = 1920 if is_final else 1280
    height = 1080 if is_final else 720
    samples = 6 if is_final else 2
    output_video = os.path.join(RENDERS_DIR, "cenit_lab_walkthrough.mp4" if is_final else "preview.mp4")

    print(f"\n=================================================================")
    print(f"[*] Starting Walkthrough Video Pipeline: Mode = {mode.upper()}")
    print(f"[*] Resolution: {width}x{height} @ {FPS} fps | Samples: {samples}")
    print(f"[*] Output: {output_video}")
    print(f"=================================================================\n")

    # Step A: Generate Shot 1 Ken Burns photos
    shot1_video = os.path.join(RENDERS_DIR, "test", "shot1_photos.mp4")
    has_shot1 = generate_shot1_photos(shot1_video, width=width, height=height)

    # Step B: Render 3D Animation (frames 145 to 1440)
    frames_dir = os.path.join(RENDERS_DIR, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    scene.render.engine = 'BLENDER_EEVEE'
    scene.eevee.use_raytracing = False
    scene.eevee.taa_render_samples = samples
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100

    scene.render.filepath = os.path.join(frames_dir, "frame_")
    scene.render.image_settings.file_format = 'PNG'

    # Render frames 145 to 1440 (Shots 2 to 7)
    start_3d = SHOT_RANGES[2][0]  # 145
    end_3d = SHOT_RANGES[7][1]    # 1440
    scene.frame_start = start_3d
    scene.frame_end = end_3d

    print(f"[*] Rendering 3D animation sequence (frames {start_3d}..{end_3d})...")
    t0 = time.time()
    bpy.ops.render.render(animation=True)
    t_render = time.time() - t0
    print(f"[+] 3D render finished in {t_render:.1f}s ({t_render / (end_3d - start_3d + 1):.2f}s per frame)")

    # Step C: Assemble 3D frames into an intermediate MP4
    shot3d_video = os.path.join(RENDERS_DIR, "test", "shots_3d.mp4")
    cmd_3d = [
        "C:\\ffmpeg\\bin\\ffmpeg.exe", "-y",
        "-framerate", str(FPS),
        "-start_number", str(start_3d),
        "-i", os.path.join(frames_dir, "frame_%04d.png"),
        "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
        shot3d_video
    ]
    subprocess.run(cmd_3d, check=True)

    # Step D: Combine Shot 1 and 3D shots with crossfade dissolve into final MP4
    if has_shot1 and os.path.exists(shot1_video):
        print("[*] Compositing Shot 1 and 3D walkthrough with crossfade...")
        # Crossfade transition (0.5s = 12 frames)
        cmd_final = [
            "C:\\ffmpeg\\bin\\ffmpeg.exe", "-y",
            "-i", shot1_video,
            "-i", shot3d_video,
            "-filter_complex", "xfade=transition=fade:duration=0.5:offset=5.5",
            "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p",
            output_video
        ]
        subprocess.run(cmd_final, check=True)
    else:
        print("[*] Outputting 3D walkthrough directly...")
        import shutil
        shutil.copyfile(shot3d_video, output_video)

    print(f"\n[+] SUCCESS! Walkthrough video created: {output_video}")


# ============================================================================
def main():
    print("[*] Calibrating lighting and color management...")
    calibrate_lighting_and_exposure()
    print("[*] Initializing scene elements, cameras, labels, and pulse...")
    setup_cameras()
    setup_labels()
    setup_infrastructure_pulse()
    setup_ceiling_animation()
    setup_shot7_plan_overlay()
    setup_compositor()
    print("[+] All walkthrough scene elements created successfully.")

    # Parse CLI flags passed after '--'
    args = []
    if "--" in sys.argv:
        args = sys.argv[sys.argv.index("--") + 1:]

    if "--test-stills" in args:
        render_test_stills()
    elif "--final" in args:
        render_and_assemble(mode="final")
    elif "--preview" in args:
        render_and_assemble(mode="preview")
    else:
        # Default: render test stills to verify all shots cleanly first
        render_test_stills()


if __name__ in ("__main__", "<run_path>"):
    main()
