import bpy
import bmesh
from mathutils import Vector
import struct

# ----------------------------
# CONFIG
# ----------------------------

MAX_VERTICES = 255

# ----------------------------
# MATERIAL PRESETS
# ----------------------------

ENGINE_MATERIAL_PRESETS = {
    "DEFAULT": {"flags": 0x00, "textured": True, "alpha": False, "extra_header": b""},
    "SPECULAR": {"flags": 0x08, "textured": True, "alpha": False, "extra_header": b"\xFF\xFF\xFF\x64"},
    "ENVMAP": {"flags": 0x18, "textured": True, "alpha": False, "extra_header": b"\xFF\xFF\xFF\x64\xFF\xFF\xFF\x00"},
    "LIGHT": {"flags": 0x04, "textured": True, "alpha": False, "extra_header": b"\xFF\xFF\xFF\xFF"},
    "ALPHA": {"flags": 0x00, "textured": True, "alpha": True, "extra_header": b""},
    "CHROME": {"flags": 0x38, "textured": False, "alpha": False, "extra_header": b"\x8D\x89\x80\xFF\xFF\xFF\xFF\x28\xFF\xFF\xFF\x01"},
    "GLASS": {"flags": 0x1A, "textured": False, "alpha": True, "extra_header": b"\x00\x00\x00\x7A\xFF\xFF\xFF\xC8\xFF\xFF\xFF\x00"},
    "WINDSHIELD": {"flags": 0x1A, "textured": False, "alpha": True, "extra_header": b"\x3B\x25\x23\xE5\xFF\xFF\xFF\x1E\xFF\xFF\xFF\x00"},
    "INVISIBLE": {"flags": 0x00, "textured": False, "alpha": False, "extra_header": b"\xFF\xFF\xFF\xFF"},
    "WINDSHIELD_INSIDE": {"flags": 0x02, "textured": False, "alpha": True, "extra_header": b"\x3B\x25\x23\xE5"},
}

# ----------------------------
# HELPERS
# ----------------------------

def clamp(n, minv, maxv):
    return max(min(maxv, n), minv)


def triangulate_object(obj):
    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()


def get_vertex_key(co, normal, uv):
    return (
        round(co.x, 6), round(co.y, 6), round(co.z, 6),
        round(normal.x, 6), round(normal.y, 6), round(normal.z, 6),
        round(uv.x, 6), round(uv.y, 6)
    )

# ----------------------------
# BUILD CHUNKS PER OBJECT
# ----------------------------

def build_chunks_from_object(obj):

    triangulate_object(obj)
    mesh = obj.data
    mesh.calc_normals_split()

    uv_layer = mesh.uv_layers.active.data if mesh.uv_layers.active else None
    material_tris = {}

    for poly in mesh.polygons:

        material = mesh.materials[poly.material_index]
        if material not in material_tris:
            material_tris[material] = []

        tri = []

        for li in poly.loop_indices:
            v = mesh.vertices[mesh.loops[li].vertex_index]

            co = v.co.copy()
            normal = mesh.loops[li].normal.copy()
            uv = uv_layer[li].uv.copy() if uv_layer else Vector((0.0, 0.0))

            tri.append((co, normal, uv))

        material_tris[material].append(tri)

    chunks = []

    for material, tris in material_tris.items():

        current_tris = []
        vert_lookup = {}

        for tri in tris:

            tri_keys = [get_vertex_key(*v) for v in tri]
            new_verts = sum(1 for k in tri_keys if k not in vert_lookup)

            if len(vert_lookup) + new_verts > MAX_VERTICES:
                if current_tris:
                    chunks.append(build_chunk_data(material, current_tris))
                current_tris = []
                vert_lookup = {}

            current_tris.append(tri)
            for key in tri_keys:
                vert_lookup[key] = True

        if current_tris:
            chunks.append(build_chunk_data(material, current_tris))

    return chunks


def build_chunk_data(material, triangles):

    verts = []
    vert_lookup = {}
    indexed_tris = []

    for tri in triangles:

        face_indices = []

        for co, normal, uv in tri:

            key = get_vertex_key(co, normal, uv)

            if key not in vert_lookup:
                vert_lookup[key] = len(verts)
                verts.append((co, normal, uv))

            face_indices.append(vert_lookup[key])

        indexed_tris.append(tuple(face_indices))

    return {
        "material": material,
        "positions": [v[0] for v in verts],
        "normals": [v[1] for v in verts],
        "uvs": [v[2] for v in verts],
        "triangles": indexed_tris
    }

# ----------------------------
# STRIP GENERATION
# ----------------------------

def generate_strips_from_tris(triangles):
    try:
        from pyffi.utils.tristrip import stripify
    except ImportError:
        print("PyFFI not installed.")
        return []

    if not triangles:
        return []

    try:
        return stripify(triangles)
    except TypeError:
        flat = [i for tri in triangles for i in tri]
        return stripify(flat)

# ----------------------------
# MULTI OBJECT EXPORT
# ----------------------------

def export_binary_multi(objects, strip_path, vertex_path):

    # Prebuild strips
    for obj in objects:
        for mesh in obj["meshes"]:

            strips = generate_strips_from_tris(mesh["triangles"])

            strip_blob = bytearray()

            for strip in strips:
                strip_blob += struct.pack("<H", len(strip))
                for index in strip:
                    strip_blob += struct.pack("<H", index)

            mesh["strips"] = strips
            mesh["strip_blob"] = strip_blob
            mesh["vertex_count"] = len(mesh["positions"])
            mesh["strip_count"] = len(strips)

    with open(strip_path, "wb") as strip_file:

        object_count = len(objects)

        strip_file.write(struct.pack("<I", object_count + 1))
        strip_file.write(b"\x00" * (88 - 4))

        # ---------------- OBJECT DEFINITIONS ----------------

        global_mesh_index = 0

        #print("\n=== OBJECT MESH INDEX MAP ===")

        for obj_index, obj in enumerate(objects):

            min_v = obj["min"]
            max_v = obj["max"]
            radius = obj["radius"]

            # Split meshes by alpha
            opaque = []
            alpha = []

            for mesh in obj["meshes"]:
                preset = ENGINE_MATERIAL_PRESETS[mesh["material"].engine_preset]
                if preset["alpha"]:
                    alpha.append(mesh)
                else:
                    opaque.append(mesh)

            opaque_count = len(opaque)
            alpha_count = len(alpha)
            total_meshes = opaque_count + alpha_count

            start_index = global_mesh_index
            end_index = global_mesh_index + total_meshes - 1 if total_meshes > 0 else global_mesh_index

            #print(f"Object {obj_index}: '{obj['name']}'")
            #print(f"  Mesh Count: {total_meshes}")
            #print(f"  Mesh Index Range: {start_index} � {end_index}")

            min_v = obj["min"]
            max_v = obj["max"]
            radius = obj["radius"]

            # ----------------------------
            # 16 bytes padding
            # ----------------------------
            strip_file.write(b"\x00" * 16)

            # ----------------------------
            # 7 floats (28 bytes)
            # radius + bounding box
            # ----------------------------
            strip_file.write(struct.pack("<f", radius))

            strip_file.write(struct.pack("<f", min_v.x))
            strip_file.write(struct.pack("<f", max_v.x))
            strip_file.write(struct.pack("<f", min_v.y))
            strip_file.write(struct.pack("<f", max_v.y))
            strip_file.write(struct.pack("<f", min_v.z))
            strip_file.write(struct.pack("<f", max_v.z))

            # Split meshes by alpha
            opaque = []
            alpha = []

            for mesh in obj["meshes"]:
                preset = ENGINE_MATERIAL_PRESETS[mesh["material"].engine_preset]
                if preset["alpha"]:
                    alpha.append(mesh)
                else:
                    opaque.append(mesh)

            opaque_count = len(opaque)
            alpha_count = len(alpha)

            hierarchy_value = 6
            unknown_value = 0

            # ----------------------------
            # OPAQUE BLOCK (16 bytes)
            # ----------------------------
            strip_file.write(struct.pack("<H", opaque_count))
            strip_file.write(struct.pack("<B", hierarchy_value))
            strip_file.write(struct.pack("<B", unknown_value))
            strip_file.write(struct.pack("<I", 0))  # padding
            strip_file.write(struct.pack("<I", global_mesh_index))
            strip_file.write(struct.pack("<I", 0))

            # ----------------------------
            # ALPHA BLOCK (16 bytes)
            # ----------------------------
            strip_file.write(struct.pack("<H", alpha_count))
            strip_file.write(struct.pack("<B", hierarchy_value))
            strip_file.write(struct.pack("<B", unknown_value))
            strip_file.write(struct.pack("<I", 0))
            strip_file.write(struct.pack("<I", global_mesh_index + opaque_count))
            strip_file.write(struct.pack("<I", 0))

            # Increment global mesh index
            global_mesh_index += (opaque_count + alpha_count)

            # ----------------------------
            # Final 16 bytes padding
            # ----------------------------
            strip_file.write(b"\x00" * 12)

        # ---------------- MESH DEFINITIONS ----------------

        ordered_meshes = []
        
        strip_file.write(b"\x00" * 4)

        for obj in objects:

            opaque = []
            alpha = []

            for mesh in obj["meshes"]:
                preset = ENGINE_MATERIAL_PRESETS[mesh["material"].engine_preset]
                if preset["alpha"]:
                    alpha.append(mesh)
                else:
                    opaque.append(mesh)

            ordered = sorted(opaque, key=lambda m: m["material"].name) + \
                      sorted(alpha, key=lambda m: m["material"].name)

            ordered_meshes.extend(ordered)

            for mesh in ordered:

                mat = mesh["material"]
                preset = ENGINE_MATERIAL_PRESETS[mat.engine_preset]

                tex_index = mat.texture_index if preset["textured"] else 0xFFFF

                header = struct.pack(
                    "<BBHHH",
                    preset["flags"],
                    0xF0,
                    tex_index,
                    mesh["vertex_count"],
                    mesh["strip_count"]
                )

                header += b"\x00" * 8
                header += preset["extra_header"]

                strip_file.write(header)

        # ---------------- STRIP DATA ----------------

        for mesh in ordered_meshes:
            strip_file.write(mesh["strip_blob"])

    # ---------------- VERTEX FILE ----------------

    with open(vertex_path, "wb") as vertex_file:

        for mesh in ordered_meshes:

            mat = mesh["material"]
            preset = ENGINE_MATERIAL_PRESETS[mat.engine_preset]

            for co in mesh["positions"]:
                vertex_file.write(struct.pack("<fff", *co))

            for n in mesh["normals"]:
                nx = clamp(int(round(n[0] * 127)), -128, 127)
                ny = clamp(int(round(n[1] * 127)), -128, 127)
                nz = clamp(int(round(n[2] * 127)), -128, 127)
                vertex_file.write(struct.pack("bbbB", nx, ny, nz, 0))

            if preset["textured"]:
                for uv in mesh["uvs"]:
                    u = clamp(int(uv[0] * 2048), -32768, 32767)
                    v = clamp(int((1.0 - uv[1]) * 2048), -32768, 32767)
                    vertex_file.write(struct.pack("<hh", u, v))

    print("Multi-object export complete.")

# ----------------------------
# MAIN
# ----------------------------

def run_export(selected, f_path, v_path):
	
	export_objects = []
	
	for obj in selected:
		chunks = build_chunks_from_object(obj)
		
		all_positions = []
		for chunk in chunks:
			all_positions.extend(chunk["positions"])
	
		if not all_positions:
			continue
	
		min_v = Vector(all_positions[0])
		max_v = Vector(all_positions[0])
	
		for co in all_positions:
			v = Vector(co)
			min_v.x = min(min_v.x, v.x)
			min_v.y = min(min_v.y, v.y)
			min_v.z = min(min_v.z, v.z)
			max_v.x = max(max_v.x, v.x)
			max_v.y = max(max_v.y, v.y)
			max_v.z = max(max_v.z, v.z)
	
		center = (min_v + max_v) * 0.5
		radius = max((Vector(co) - center).length for co in all_positions)
	
		export_objects.append({
			"name": obj.name,
			"min": min_v,
			"max": max_v,
			"radius": radius,
			"meshes": chunks
		})
	
	export_binary_multi(
		export_objects,
		f_path,
		v_path
	)