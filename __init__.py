bl_info = {
    "name": "XBX Format",
    "author": "voliver9",
    "version": (1, 0, 2),
    "blender": (3, 6, 0),
    "location": "File > Import-Export",
    "description": "Import and export decompressed 3dobjs.XBX and 3dobjsp.XBX files",
    "category": "Import-Export",
}

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import IntProperty, StringProperty, PointerProperty, BoolProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper
import tempfile
import os
from . import xbx_export
from . import xbx_import
from . import xbx_obj_replace

# ----------------------------
# EXTRA PROPERTIES
# ----------------------------
def register_properties():
    if not hasattr(bpy.types.Material, "engine_preset"):
        bpy.types.Material.engine_preset = bpy.props.EnumProperty(
            name="Material Preset",
            items=[
                ('DEFAULT', "Default", "Description?"),
                ('SPECULAR', "Specular", ""),
                ('ENVMAP', "Specular + env. map", ""),
                ('LIGHT', "Light", ""),
                ('ALPHA', "Texture with alpha", ""),
                ('CHROME', "Chrome", ""),
                ('GLASS', "Clear glass", ""),
                ('WINDSHIELD', "Windshield", ""),
                ('INVISIBLE', "Shadow mesh / Boundary", ""),
                ('WINDSHIELD_INSIDE', "Windshield (no reflections)", ""),
            ],
            default='DEFAULT'
        )
    if not hasattr(bpy.types.Material, "texture_index"):
        bpy.types.Material.texture_index = IntProperty(
            name="Texture Index",
            default=0,
            min=0,
            max=65535
        )
    if not hasattr(bpy.types.Object, "target_index"):
        bpy.types.Object.target_index = IntProperty(
            name="Target Object Index",
            description="Index in target 3dobjs file",
            default=0,
            min=0
        )

def unregister_properties():
    if hasattr(bpy.types.Material, "engine_preset"):
        del bpy.types.Material.engine_preset
    if hasattr(bpy.types.Material, "texture_index"):
        del bpy.types.Material.texture_index
    if hasattr(bpy.types.Object, "target_index"):
        del bpy.types.Object.target_index

class GAME_PT_object(bpy.types.Panel):
    bl_label = "XBX Object"
    bl_idname = "GAME_PT_object"
    bl_space_type = 'PROPERTIES'      # Properties editor
    bl_region_type = 'WINDOW'
    bl_context = 'object'             # Object tab

    def draw(self, context):
        layout = self.layout
        obj = context.object
        layout.prop(obj, "target_index")

class ENGINE_PT_material_panel(Panel):
    bl_label = "XBX Export Settings"
    bl_idname = "ENGINE_PT_material_panel"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"

    def draw(self, context):
        layout = self.layout
        mat = context.material
        if not mat:
            return
        layout.prop(mat, "engine_preset")
        layout.prop(mat, "texture_index")

# ----------------------------
# IMPORT OPERATOR
# ----------------------------

class ImportGameMesh(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.game_mesh"
    bl_label = "Import XBX (Decompressed)"

    filename_ext = ".dat"
    filter_glob: bpy.props.StringProperty(
        default="*.dat",
        options={'HIDDEN'}
    )

    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement
    )

    directory: bpy.props.StringProperty(
        subtype='DIR_PATH'
    )

    merge_submeshes: bpy.props.BoolProperty(
        name="Merge Submeshes",
        default=False
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "merge_submeshes")

    def execute(self, context):

        if len(self.files) != 2:
            self.report({'ERROR'}, "Select exactly two .dat files (vertex and face)")
            return {'CANCELLED'}

        paths = [os.path.join(self.directory, f.name) for f in self.files]

        # Optional: auto-detect which is which
        vertex_path = None
        face_path = None

        for p in paths:
            if p.lower().endswith("_v.dat"):
                vertex_path = p
            elif p.lower().endswith("_f.dat"):
                face_path = p

        # fallback if no naming convention
        if not vertex_path or not face_path:
            vertex_path, face_path = paths

        try:
            xbx_import.run_import(
                vertex_path,
                face_path,
                self.merge_submeshes
            )
        
        except Exception as e:
            self.report({'ERROR'}, f"Import failed: {e}")
            return {'CANCELLED'}

        self.report({'INFO'}, "Mesh imported successfully")
        return {'FINISHED'}

# ----------------------------
# EXPORT STANDALONE OPERATOR
# ----------------------------

class ExportGameMeshStandalone(Operator, ExportHelper):
    bl_idname = "export_scene.game_mesh"
    bl_label = "Export XBX (Standalone decompressed)"
    filename_ext = ".dat"
    filter_glob: StringProperty(default="*.dat", options={'HIDDEN'})

    def execute(self, context):

        # Ensure we're in Object mode (important for exporters)
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        selected = [
            obj for obj in context.view_layer.objects.selected
            if obj.type == 'MESH'
        ]

        if not selected:
            self.report({'ERROR'}, "No mesh objects selected")
            return {'CANCELLED'}

        selected_sorted = sorted(selected, key=lambda o: o.name)

        # -------- VALIDATION BLOCK --------
        for obj in selected_sorted:
            mesh = obj.data

            if mesh is None:
                self.report({'ERROR'}, f"{obj.name} has no mesh data")
                return {'CANCELLED'}

            if len(mesh.polygons) == 0:
                self.report({'ERROR'}, f"{obj.name} has no polygons")
                return {'CANCELLED'}

            if not mesh.uv_layers:
                self.report({'ERROR'}, f"{obj.name} has no UV layers")
                return {'CANCELLED'}

            if not mesh.materials:
                self.report({'ERROR'}, f"{obj.name} has no materials assigned")
                return {'CANCELLED'}

        # -------- FILE PATH HANDLING --------
        base_path = self.filepath
        if base_path.lower().endswith(".dat"):
            base_path = base_path[:-4]

        vertex_path = f"{base_path}_v.dat"
        face_path = f"{base_path}_f.dat"

        # -------- EXPORT --------
        try:
            xbx_export.run_export(selected_sorted, face_path, vertex_path)

        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

        self.report(
            {'INFO'},
            f"Exported:\n Vertex: {vertex_path}\n Faces: {face_path}"
        )

        return {'FINISHED'}

# ----------------------------
# EXPORT INTO OPERATOR 
# ----------------------------

class ExportGameMeshInto(Operator, ImportHelper):
    bl_idname = "export_scene.game_mesh_into"
    bl_label = "Export XBX (Into Existing Scene)"

    filename_ext = ".dat"
    filter_glob: StringProperty(default="*.dat", options={'HIDDEN'})

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    def execute(self, context):

        # Ensure we're in Object mode
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # Gather selected mesh objects
        selected_sorted = sorted(
            [obj for obj in context.view_layer.objects.selected if obj.type == 'MESH'],
            key=lambda o: o.name
        )

        if not selected_sorted:
            self.report({'ERROR'}, "No mesh objects selected")
            return {'CANCELLED'}

        # Validate meshes
        for obj in selected_sorted:
            mesh = obj.data
            if mesh is None:
                self.report({'ERROR'}, f"{obj.name} has no mesh data")
                return {'CANCELLED'}
            if len(mesh.polygons) == 0:
                self.report({'ERROR'}, f"{obj.name} has no polygons")
                return {'CANCELLED'}
            if not mesh.uv_layers:
                self.report({'ERROR'}, f"{obj.name} has no UV layers")
                return {'CANCELLED'}
            if not mesh.materials:
                self.report({'ERROR'}, f"{obj.name} has no materials assigned")
                return {'CANCELLED'}

        # Check that exactly two files were selected
        if len(self.files) != 2:
            self.report({'ERROR'}, "Select exactly two .dat files (vertex and face)")
            return {'CANCELLED'}

        paths = [os.path.join(self.directory, f.name) for f in self.files]

        # Auto-detect vertex and face files
        vertex_path = next((p for p in paths if p.lower().endswith("_v.dat")), None)
        face_path = next((p for p in paths if p.lower().endswith("_f.dat")), None)

        # Fallback if naming convention is not followed
        if not vertex_path or not face_path:
            vertex_path, face_path = paths

        # Export to temporary files
        tmp_vertex = tempfile.NamedTemporaryFile(delete=False, suffix=".dat")
        tmp_face = tempfile.NamedTemporaryFile(delete=False, suffix=".dat")
        tmp_vertex_path = tmp_vertex.name
        tmp_face_path = tmp_face.name
        tmp_vertex.close()
        tmp_face.close()

        try:
            # Run export to temp files
            xbx_export.run_export(selected_sorted, tmp_face_path, tmp_vertex_path)

            # Build object index lists
            target_object_indices = [obj.target_index for obj in selected_sorted]
            source_object_indices = list(range(len(selected_sorted)))

            # Sort descending by target indices
            sorted_pairs = sorted(
                zip(target_object_indices, source_object_indices),
                reverse=True
            )

            target_object_indices, source_object_indices = zip(*sorted_pairs)

            # Run the replace
            xbx_obj_replace.run_replace(
                vertex_path,
                face_path,
                tmp_vertex_path,
                tmp_face_path,
                list(target_object_indices),
                list(source_object_indices)
            )

        except Exception as e:
            traceback.print_exc()
            self.report({'ERROR'}, f"Export into failed: {e}")
            return {'CANCELLED'}

        finally:
            # Cleanup temp files
            for path in (tmp_vertex_path, tmp_face_path):
                if os.path.exists(path):
                    os.remove(path)

        self.report({'INFO'}, "Exported into target files successfully")
        return {'FINISHED'}

# ----------------------------
# MENUS
# ----------------------------
def menu_func_import(self, context):
    self.layout.operator(ImportGameMesh.bl_idname, text="Decompressed XBX")

def menu_func_export(self, context):
    self.layout.operator(ExportGameMeshStandalone.bl_idname, text="Decompressed XBX")
    self.layout.operator(ExportGameMeshInto.bl_idname, text="Decompressed XBX (Into existing)")

# ----------------------------
# REGISTER/UNREGISTER
# ----------------------------
classes = (
    GAME_PT_object,
    ENGINE_PT_material_panel,
    ImportGameMesh,
    ExportGameMeshStandalone,
    ExportGameMeshInto,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Extra properties
    register_properties()

    # Menus
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    unregister_properties()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)