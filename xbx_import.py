import bpy
import struct
import os
import math
# =====================================
# DEBUG HEX LOGGERZ
# =====================================

DEBUG_LOG = False
LOG_FILE_PATH = r"d:\saved\cockracinglog.txt"

def log_read(name, offset, raw, value):
	if DEBUG_LOG:
		# Ensure the directory exists
		log_dir = os.path.dirname(LOG_FILE_PATH)
		if log_dir and not os.path.exists(log_dir):
			os.makedirs(log_dir)

		hex_bytes = " ".join(f"{b:02X}" for b in raw)
		message = (
			f"{name:<8} "
			f"OFF 0x{offset:08X} ({offset}) "
			f"HEX [{hex_bytes}] "
			f"VAL {value}\n"
		)
		
		# Append to the text file
		with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
			f.write(message)
			
		# Optional: Keep printing to Blender console as well for live viewing
		print(message.strip())

# =====================================
# HELPERS
# =====================================

def normalize(v):
	x, y, z = v
	length = math.sqrt(x*x + y*y + z*z)

	if length == 0:
		return (0.0, 0.0, 0.0)

	return (
		x / length,
		y / length,
		z / length
	)

def decode_normal(v):
	return v / 127.0
	
def read_draw_command(fdata, offset):
	start = offset

	raw = fdata[offset:offset + 16]

	material_flags = fdata[offset]
	opcode = fdata[offset + 1]
	offset += 2

	texture_index, vertex_draw_count, strip_count = struct.unpack_from("<HHH", fdata, offset)
	offset += 6

	offset += 8  # padding

	log_read(
		"CMD",
		start,
		raw,
		f"Flags=0x{material_flags:02X} "
		f"Opcode=0x{opcode:02X} "
		f"Tex={texture_index} "
		f"Verts={vertex_draw_count} "
		f"Strips={strip_count}"
	)

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
	if len(normals) == len(mesh.vertices):
		mesh.use_auto_smooth = True
		mesh.normals_split_custom_set_from_vertices(normals)

	mesh.update()
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
			start = offset

			raw = vdata[offset:offset+12]

			x, y, z = struct.unpack_from("<fff", vdata, offset)

			log_read(
				f"POS[{i}]",
				start,
				raw,
				f"X={x} Y={y} Z={z}"
			)

			positions.append((x, -z, y))
			offset += 12

		normals = []

		for i in range(vertex_count):
			start = offset

			raw = vdata[offset:offset+4]

			nx, ny, nz, pad = struct.unpack_from("<bbbB", vdata, offset)
			# NY NX NZ is correct but direction is wrong
			# NZ NY NX also correct? but direction is wrong too
			if nx == -128 or ny == -128 or nz == -128:
				print(
					"NORMAL -128 FOUND",
					nx, ny, nz,
				"offset", hex(offset)
				)
				raise ValueError("Invalid normal value -128")
			normal = normalize((
				decode_normal(nx),
				decode_normal(-nz),
				decode_normal(ny)
			))

			log_read(
				f"NORM[{i}]",
				start,
				raw,
				f"RAW({nx},{ny},{nz},{pad}) -> {normal}"
			)

			normals.append(normal)
			offset += 4

		uvs = []
		if texture_index == 0xFFFF:
			for i in range(vertex_count):
				uvs.append((0.0, 0.0))
		else:
			for i in range(vertex_count):
				start = offset

				raw = vdata[offset:offset+4]

				u, v = struct.unpack_from("<HH", vdata, offset)

				uv = (
					u / 2048.0,
					1.0 - (v / 2048.0)
				)

				log_read(
					f"UV[{i}]",
					start,
					raw,
					f"RAW({u},{v}) -> {uv}"
				)

				offset += 4
				uvs.append(uv)

		# Per-vertex extra data (flag 0x40)
		if cmd["flags"] & (1 << 6):
			offset += vertex_count * 4

		vertex_offset = offset

		# =====================================
		# READ STRIPS (SEPARATE REGION)
		# =====================================

		faces = []

		for s in range(strip_count):

			start = strips_offset
			raw = fdata[strips_offset:strips_offset + 2]

			strip_length = struct.unpack_from("<H", fdata, strips_offset)[0]

			log_read(
				f"STRIP[{s}]",
				start,
				raw,
				f"Length={strip_length}"
			)

			strips_offset += 2

			start = strips_offset
			raw = fdata[strips_offset:strips_offset + strip_length * 2]

			strip_indices = struct.unpack_from(
				f"<{strip_length}H",
				fdata,
				strips_offset
			)

			log_read(
				f"INDEX[{s}]",
				start,
				raw,
				strip_indices
			)

			strips_offset += strip_length * 2

			for i in range(strip_length - 2):
				a = strip_indices[i]
				b = strip_indices[i + 1]
				c = strip_indices[i + 2]

				if a != b and b != c and a != c:

					if i % 2 == 0:
						face = (a, b, c)
					else:
						face = (b, a, c)

					faces.append(face)

					log_read(
						f"FACE[{s}]",
						start,
						b"",
						f"{i}: {face}"
					)

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
