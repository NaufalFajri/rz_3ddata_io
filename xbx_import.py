import bpy
import struct

# =====================================
# HELPERS
# =====================================

def read_draw_command(fdata, offset):
    material_flags = fdata[offset]
    opcode = fdata[offset + 1]
    offset += 2

    texture_index, vertex_draw_count, strip_count = struct.unpack_from("<HHH", fdata, offset)
    offset += 6
    
    offset += 8 # padding

    # Extra data from flags
    if material_flags & 0x04:
        offset += 4
    if material_flags & 0x08:
        offset += 4
    if material_flags & 0x10:
        offset += 4
    if material_flags & 0x40:
        offset += 4
    if texture_index == 65535:
        offset += 4

    return {
        "flags": material_flags,
        "opcode": opcode,
        "texture": texture_index,
        "vertex_count": vertex_draw_count,
        "strip_count": strip_count,
    }, offset


def create_mesh(name, positions, normals, uvs, faces, obj_index):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata(positions, [], faces)
    mesh.update()

    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)

    #mesh.create_normals_split()
    mesh.normals_split_custom_set_from_vertices(normals)

    uv_layer = mesh.uv_layers.new(name="UVMap")

    for poly in mesh.polygons:
        for loop_index in poly.loop_indices:
            vertex_index = mesh.loops[loop_index].vertex_index
            uv_layer.data[loop_index].uv = uvs[vertex_index]

    obj.target_index = obj_index
    #mesh.use_auto_smooth = True


			


			
def run_import(vertex_file, face_file, merge_per_object):
	# =====================================
	# READ FILES
	# =====================================

	with open(face_file, "rb") as f:
		fdata = f.read()

	with open(vertex_file, "rb") as f:
		vdata = f.read()


	# =====================================
	# OBJECT TABLE
	# =====================================

	object_amount = struct.unpack_from("<I", fdata, 0)[0]
	object_bounds = []

	offset = 156

	for i in range(object_amount - 1):
		command_start = struct.unpack_from("<I", fdata, offset)[0]
		command_count = struct.unpack_from("<H", fdata, offset - 8)[0]
		object_bounds.append(command_start + command_count)
		offset += 88

	total_commands = object_bounds[-1]

	# =====================================
	# PASS 1 � PARSE COMMAND BLOCK ONLY
	# =====================================

	commands_offset = object_amount * 88 + 4
	parsed_commands = []

	for i in range(total_commands):
		cmd, commands_offset = read_draw_command(fdata, commands_offset)
		parsed_commands.append(cmd)

	# After last command, strip block begins
	strips_offset = commands_offset

	# =====================================
	# PASS 2 � BUILD MESHES
	# =====================================

	vertex_offset = 0
	current_object = 0

	merged_positions = []
	merged_normals = []
	merged_uvs = []
	merged_faces = []
	vertex_base_index = 0

	for cmdindex, cmd in enumerate(parsed_commands):

		if cmdindex == object_bounds[current_object]:
			current_object += 1

		texture_index = cmd["texture"]
		vertex_count = cmd["vertex_count"]
		strip_count = cmd["strip_count"]

		object_name = f"OBJ{current_object}_CMD[{cmdindex}]_{texture_index}"

		# =====================================
		# READ VERTICES
		# =====================================

		offset = vertex_offset

		positions = []
		for i in range(vertex_count):
			x, y, z = struct.unpack_from("<fff", vdata, offset)
			positions.append((x, y, z))
			offset += 12

		normals = []
		for i in range(vertex_count):
			nx, ny, nz, pad = struct.unpack_from("<bbbB", vdata, offset)
			offset += 4
			normals.append((nx / 128.0, nz / 128.0, ny / 128.0))

		uvs = []
		if texture_index == 0xFFFF:
			for i in range(vertex_count):
				uvs.append((0.0, 0.0))
		else:
			for i in range(vertex_count):
				u, v = struct.unpack_from("<HH", vdata, offset)
				offset += 4
				uvs.append((u / 2048.0, 1.0 - (v / 2048.0)))

		# Per-vertex extra data (flag 0x40)
		if cmd["flags"] & (1 << 6):
			offset += vertex_count * 4

		vertex_offset = offset

		# =====================================
		# READ STRIPS (SEPARATE REGION)
		# =====================================

		faces = []

		for s in range(strip_count):
			strip_length = struct.unpack_from("<H", fdata, strips_offset)[0]
			strips_offset += 2

			strip_indices = struct.unpack_from(f"<{strip_length}H", fdata, strips_offset)
			strips_offset += strip_length * 2

			for i in range(strip_length - 2):
				a = strip_indices[i]
				b = strip_indices[i + 1]
				c = strip_indices[i + 2]

				if a != b and b != c and a != c:
					if i % 2 == 0:
						faces.append((a, b, c))
					else:
						faces.append((b, a, c))

		# =====================================
		# MERGE OR CREATE
		# =====================================

		if merge_per_object:

			adjusted_faces = [
				(a + vertex_base_index, b + vertex_base_index, c + vertex_base_index)
				for (a, b, c) in faces
			]

			merged_positions.extend(positions)
			merged_normals.extend(normals)
			merged_uvs.extend(uvs)
			merged_faces.extend(adjusted_faces)

			vertex_base_index += len(positions)

			is_last_command = (cmdindex + 1 == object_bounds[current_object])

			if is_last_command:
				create_mesh(
					f"OBJ{current_object}",
					merged_positions,
					merged_normals,
					merged_uvs,
					merged_faces,
					current_object
				)

				merged_positions = []
				merged_normals = []
				merged_uvs = []
				merged_faces = []
				vertex_base_index = 0

		else:
			create_mesh(object_name, positions, normals, uvs, faces, current_object)