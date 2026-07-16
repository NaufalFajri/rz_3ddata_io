import bpy
import struct
from pathlib import Path

# =====================================
# HELPERS
# =====================================

def replace_bytes(TARGET_FILE, SOURCE_FILE, TARGET_OFFSET, TARGET_END, SOURCE_OFFSET, SOURCE_END):
    target_path = Path(TARGET_FILE)
    source_path = Path(SOURCE_FILE)

    # Read source file
    with source_path.open("rb") as f:
        src_data = f.read()

    if SOURCE_END > len(src_data):
        raise ValueError("Source range exceeds source file size.")

    replacement = src_data[SOURCE_OFFSET:SOURCE_END]

    # Open target file for read/write binary
    with target_path.open("r+b") as f:
        tgt_data = f.read()

        if TARGET_END > len(tgt_data):
            raise ValueError("Target range exceeds target file size.")

        # Build new content
        new_data = (
            tgt_data[:TARGET_OFFSET] +
            replacement +
            tgt_data[TARGET_END:]
        )

        # Go to start and overwrite file
        f.seek(0)
        f.write(new_data)
        f.truncate()  # Adjust file size if changed
        
def replace_object(TARGET_FILE, SOURCE_FILE, TARGET_OFFSET, SOURCE_OFFSET):
    target_path = Path(TARGET_FILE)
    source_path = Path(SOURCE_FILE)

    # Read source file
    with source_path.open("rb") as f:
        src_data = f.read()

    # Open target file for read/write binary
    with target_path.open("r+b") as f:
        tgt_data = f.read()

        # Build new content
        new_data = (
            tgt_data[:TARGET_OFFSET] +
            src_data[SOURCE_OFFSET:(SOURCE_OFFSET + 46)] +
            tgt_data[(SOURCE_OFFSET + 46):(SOURCE_OFFSET + 48)] +
            src_data[(SOURCE_OFFSET + 48):(SOURCE_OFFSET + 62)] +
            tgt_data[(SOURCE_OFFSET + 62):(SOURCE_OFFSET + 64)] +
            src_data[(SOURCE_OFFSET + 64):(SOURCE_OFFSET + 88)] +
            tgt_data[(TARGET_OFFSET + 88):]
        )
        
        f.seek(0)
        f.write(new_data)
        f.truncate()
        

def read_draw_command(fdata, offset):
    start_offset = offset
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
    if texture_index == 65535:
        offset += 4

    return {
        "flags": material_flags,
        "opcode": opcode,
        "texture": texture_index,
        "vertex_count": vertex_draw_count,
        "strip_count": strip_count,
        "start_offset": start_offset,
    }, offset

def read_file(vertex_file, face_file, f0_data):
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
    
    eof_data = (object_amount - 1, total_commands, strips_offset, len(fdata), len(vdata), "EOF")

    # =====================================
    # PASS 2 � BUILD MESHES
    # =====================================

    vertex_offset = 0
    current_object = 0
    vertex_base_index = 0

    for cmdindex, cmd in enumerate(parsed_commands):

        if cmdindex == object_bounds[current_object]:
            current_object += 1

        texture_index = cmd["texture"]
        vertex_count = cmd["vertex_count"]
        strip_count = cmd["strip_count"]

        # =====================================
        # READ VERTICES
        # =====================================

        offset = vertex_offset

        offset += vertex_count * 16

        if texture_index != 0xFFFF:
            offset += vertex_count * 4

        # Per-vertex extra data (flag 0x40)
        if cmd["flags"] & (1 << 6):
            offset += vertex_count * 4
            
        f0_data.append((current_object, cmdindex, cmd["start_offset"], strips_offset, vertex_offset, cmd["texture"]))

        vertex_offset = offset

        # =====================================
        # READ STRIPS (SEPARATE REGION)
        # =====================================

        for s in range(strip_count):
            strip_length = struct.unpack_from("<H", fdata, strips_offset)[0]
            strips_offset += 2

            strip_indices = struct.unpack_from(f"<{strip_length}H", fdata, strips_offset)
            strips_offset += strip_length * 2
    
    f0_data.append(eof_data)

def run_replace(target_v, target_f, source_v, source_f, target_indices, source_indices):

	target_data = []
	source_data = []
	# ^obj no., f0 no., f0 offset, strip offset, vertex offset, tex index

	read_file(target_v, target_f, target_data)
	read_file(source_v, source_f, source_data)

	for obj_index in range(len(target_indices)):
		target_f0_range = [0, 0]
		source_f0_range = [0, 0]
		in_range = 0
		
		for f0 in target_data:
			if in_range == 0 and f0[0] == target_indices[obj_index]:
				in_range = 1
				target_f0_range[0] = f0[1]
			elif in_range == 1 and f0[0] != target_indices[obj_index]:
				in_range = 0
				target_f0_range[1] = f0[1]
				break
		
		for f0 in source_data:
			if in_range == 0 and f0[0] == source_indices[obj_index]:
				in_range = 1
				source_f0_range[0] = f0[1]
			elif in_range == 1 and f0[0] != source_indices[obj_index]:
				in_range = 0
				source_f0_range[1] = f0[1]
				break
		
		#replace vertices 
		replace_bytes(target_v, source_v, target_data[target_f0_range[0]][4], target_data[target_f0_range[1]][4], source_data[source_f0_range[0]][4], source_data[source_f0_range[1]][4])
		#replace strips
		replace_bytes(target_f, source_f, target_data[target_f0_range[0]][3], target_data[target_f0_range[1]][3], source_data[source_f0_range[0]][3], source_data[source_f0_range[1]][3])
		#replace object definitions
		replace_object(target_f, source_f, (target_indices[obj_index] + 1) * 88, (source_indices[obj_index] + 1) * 88)
		
		with Path(target_f).open("rb") as f:
			fdata = bytearray(f.read())
			
		object_amount = struct.unpack_from("<I", fdata, 0)[0]
		fdata = fdata[:(4 + object_amount * 88)]
		
		for i in range(target_indices[obj_index] + 1, object_amount):
			offset = i * 88 + 52
			current_index = struct.unpack_from("<H", fdata, offset)[0]
			current_index2 = struct.unpack_from("<H", fdata, offset + 16)[0]
			target_index = struct.unpack_from("<H", fdata, offset - 72)[0] + struct.unpack_from("<H", fdata, offset - 80)[0]
			diff = target_index - current_index
			struct.pack_into("<H", fdata, offset, target_index)
			struct.pack_into("<H", fdata, offset + 16, current_index2 + diff)
			
		fdata = bytes(fdata)
		
		with Path(target_f).open("r+b") as f:
			tgt_data = f.read()

			# Build new content
			new_data = (
				fdata +
				tgt_data[(4 + object_amount * 88):]
			)
			f.seek(0)
			f.write(new_data)
			f.truncate()
			
	for obj_index in range(len(target_indices)):
		target_f0_range = [0, 0]
		source_f0_range = [0, 0]
		in_range = 0
		
		for f0 in target_data:
			if in_range == 0 and f0[0] == target_indices[obj_index]:
				in_range = 1
				target_f0_range[0] = f0[1]
			elif in_range == 1 and f0[0] != target_indices[obj_index]:
				in_range = 0
				target_f0_range[1] = f0[1]
				break
		
		for f0 in source_data:
			if in_range == 0 and f0[0] == source_indices[obj_index]:
				in_range = 1
				source_f0_range[0] = f0[1]
			elif in_range == 1 and f0[0] != source_indices[obj_index]:
				in_range = 0
				source_f0_range[1] = f0[1]
				break
		
		#replace f0s
		replace_bytes(target_f, source_f, target_data[target_f0_range[0]][2], target_data[target_f0_range[1]][2], source_data[source_f0_range[0]][2], source_data[source_f0_range[1]][2])
