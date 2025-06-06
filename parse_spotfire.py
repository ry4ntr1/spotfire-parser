#!/usr/bin/env python3
import os
import zipfile
import tempfile
import json
from lxml import etree as ET

# (1) Directories (inside container)
INPUT_DIR = "dxp_input"
OUTPUT_DIR = "im_output"

# (2) Spotfire namespace (adjust if needed)
NS = {"sf": "http://www.spotfire.com/schemas/Document1.0.xsd"}

def parse_field_value(fld, type_lookup):
    """Return a Python representation of a <sf:Field> value."""
    nested = fld.findall("sf:Object", NS)
    if nested:
        return [parse_object(n, type_lookup) for n in nested]

    children = list(fld)
    if children:
        # <Field><String Value=".."/></Field> etc.
        if len(children) == 1:
            child = children[0]
            val = child.attrib.get("Value")
            if val is not None:
                return val
            if child.tag.endswith("Array"):
                elems = child.find("sf:Elements", NS)
                if elems is not None:
                    return [parse_object(o, type_lookup) for o in elems.findall("sf:Object", NS)]
                return [c.attrib.get("Value", (c.text or "").strip()) for c in child]
        else:
            values = []
            for c in children:
                v = c.attrib.get("Value")
                if v is not None:
                    values.append(v)
            if values:
                return values
    return (fld.text or "").strip()


def parse_object(elem, type_lookup):
    """
    Recursively parse a <sf:Object> node into a Python dict.
    
    Changed: first look under <sf:Fields> for <sf:Field> children.
    """
    obj_id = elem.attrib.get("Id", "")
    type_node = elem.find("sf:Type", NS)
    obj_type = ""
    if type_node is not None:
        ref = type_node.find("sf:TypeRef", NS)
        if ref is not None:
            ref_id = ref.attrib.get("Value")
            obj_type = type_lookup.get(ref_id, "")
        else:
            to = type_node.find("sf:TypeObject", NS)
            if to is not None:
                obj_type = to.attrib.get("FullTypeName", "")
    if obj_type.startswith("Spotfire") and "." in obj_type:
        obj_type = obj_type.split(".")[-1]

    fields_dict = {}
    # ─── Look inside <sf:Fields> for all <sf:Field> children ───
    fields_container = elem.find("sf:Fields", NS)
    if fields_container is not None:
        for fld in fields_container.findall("sf:Field", NS):
            name = fld.attrib.get("Name")
            fields_dict[name] = parse_field_value(fld, type_lookup)
    # ────────────────────────────────────────────────────────────

    # Also capture any direct child <sf:Object> (not inside <sf:Fields>)
    children = [parse_object(child, type_lookup) for child in elem.findall("sf:Object", NS)]

    return {
        "Id": obj_id,
        "Type": obj_type,
        "Fields": fields_dict,
        "Children": children
    }

def build_intermediate_model(xml_root):
    """
    Walk the AnalysisDocument root and collect:
    - DataTables
    - Visualizations (BarChart, LineChart, etc.)
    - Filters / FilteringSchemes
    - Bookmarks
    - Scripts / DataFunctions
    """
    im = {
        "DataTables": [],
        "Visualizations": [],
        "Filters": [],
        "Bookmarks": [],
        "Scripts": []
    }

    type_lookup = {
        to.attrib.get("Id"): to.attrib.get("FullTypeName", "")
        for to in xml_root.findall(".//sf:TypeObject", NS)
    }
    # Lookup tables for resolving ObjectRef references
    string_lookup = {
        s.attrib.get("Id"): s.attrib.get("Value", "")
        for s in xml_root.findall(".//sf:String", NS)
    }
    data_type_lookup = {}
    for o in xml_root.findall(".//sf:Object", NS):
        ref = o.find("sf:Type/sf:TypeRef", NS)
        if ref is not None and type_lookup.get(ref.attrib.get("Value"), "").endswith("DataType"):
            name_f = o.find("sf:Fields/sf:Field[@Name='name']/sf:String", NS)
            if name_f is not None:
                data_type_lookup[o.attrib.get("Id")] = name_f.attrib.get("Value", "")

    for obj in xml_root.findall(".//sf:Object", NS):
        parsed = parse_object(obj, type_lookup)
        t = parsed["Type"]

        # 1. DataTable
        if t.endswith("DataTable"):
            dt = {
                "Id": parsed["Id"],
                "Name": parsed["Fields"].get("Name", ""),
                "DataSource": parsed["Fields"].get("DataSource", ""),
                "Transformations": [],
                "Columns": [],
                "Relationships": []
            }
            # Collect Transformations (each one is itself an <Object>)
            for trans in parsed["Fields"].get("Transformations", []):
                dt["Transformations"].append({
                    "Type": trans.get("Type", ""),
                    **trans.get("Fields", {})
                })
            # Collect Columns
            cols_field = parsed["Fields"].get("Columns", [])
            for col in cols_field:
                # Older behavior: direct DataColumn objects
                if col.get("Type") == "DataColumn":
                    fields = col.get("Fields", {})
                    name = fields.get("Name", "")
                    if isinstance(name, str):
                        name = string_lookup.get(name, name)
                    dtype = fields.get("DataType", "")
                    if isinstance(dtype, str):
                        dtype = data_type_lookup.get(dtype, string_lookup.get(dtype, dtype))
                    elif isinstance(dtype, dict):
                        dtype = dtype.get("Fields", {}).get("name", "")
                    elif isinstance(dtype, list) and dtype and isinstance(dtype[0], dict):
                        dtype = dtype[0].get("Fields", {}).get("name", "")
                    expr = fields.get("Expression", "")
                    if isinstance(expr, str):
                        expr = string_lookup.get(expr, expr)
                    dt["Columns"].append({"Name": name, "DataType": dtype, "Expression": expr})
                # Newer format: a DataColumnCollection with Items -> Nodes
                elif col.get("Type") == "DataColumnCollection":
                    for item in col.get("Fields", {}).get("Items", []):
                        nodes = item.get("Fields", {}).get("Nodes", [])
                        for node in nodes:
                            if not isinstance(node, dict):
                                continue
                            fields = node.get("Fields", {})
                            name = fields.get("Name", "")
                            if isinstance(name, str):
                                name = string_lookup.get(name, name)
                            dtype = fields.get("DataType", "")
                            if isinstance(dtype, str):
                                dtype = data_type_lookup.get(dtype, string_lookup.get(dtype, dtype))
                            elif isinstance(dtype, dict):
                                dtype = dtype.get("Fields", {}).get("name", "")
                            elif isinstance(dtype, list) and dtype and isinstance(dtype[0], dict):
                                dtype = dtype[0].get("Fields", {}).get("name", "")
                            expr = fields.get("Expression", "")
                            if isinstance(expr, str):
                                expr = string_lookup.get(expr, expr)
                            dt["Columns"].append({"Name": name, "DataType": dtype, "Expression": expr})
            # Collect Relations
            for rel in parsed["Fields"].get("Relations", []):
                dt["Relationships"].append({
                    "Type": rel.get("Type", ""),
                    **rel.get("Fields", {})
                })
            im["DataTables"].append(dt)

        # 2. Visualizations (common types)
        elif any(t.endswith(v) for v in ["BarChart", "LineChart", "Table", "ScatterChart", "PieChart"]):
            viz = {
                "Id": parsed["Id"],
                "Type": t,
                "DataTable": parsed["Fields"].get("Data", ""),
                "Bindings": {},
                "Filters": parsed["Fields"].get("Filters", ""),
                "Formatting": parsed["Fields"].get("Format", "")
            }
            # Common binding fields (adjust as needed)
            for bf in ["XAxisColumn", "YAxisColumn", "ColorBy", "CategoryField", "ValueField", "Legend"]:
                if bf in parsed["Fields"]:
                    viz["Bindings"][bf] = parsed["Fields"][bf]
            im["Visualizations"].append(viz)

        # 3. FilteringScheme or Filter
        elif t in ["FilteringScheme", "Filter"]:
            im["Filters"].append(parsed)

        # 4. Bookmark
        elif t == "Bookmark":
            im["Bookmarks"].append(parsed)

        # 5. Script / DataFunction
        elif t in ["Script", "DataFunction"]:
            im["Scripts"].append({
                "Id": parsed["Id"],
                "Type": t,
                "Content": parsed["Fields"].get("Script", parsed["Fields"].get("Expression", ""))
            })

    return im

def process_dxp(dxp_path, output_dir):
    """Unzip .dxp, locate AnalysisDocument.xml, build IM, write JSON."""
    base_name = os.path.splitext(os.path.basename(dxp_path))[0]
    with tempfile.TemporaryDirectory() as tmpdir:
        # Unzip into tmpdir
        with zipfile.ZipFile(dxp_path, "r") as z:
            z.extractall(tmpdir)

        xml_file = os.path.join(tmpdir, "AnalysisDocument.xml")
        if not os.path.isfile(xml_file):
            print(f"⚠️  Skipping {dxp_path}: AnalysisDocument.xml not found.")
            return

        # Parse XML
        try:
            tree = ET.parse(xml_file)
        except ET.XMLSyntaxError as e:
            print(f"⚠️  Skipping {dxp_path}: XML syntax error: {e}")
            return
        root = tree.getroot()
        im = build_intermediate_model(root)

        # Write JSON
        out_path = os.path.join(output_dir, f"{base_name}_IM.json")
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(im, fp, indent=2)
        print(f"✅  Parsed {dxp_path} → {out_path}")

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    for fname in os.listdir(INPUT_DIR):
        if fname.lower().endswith(".dxp"):
            full = os.path.join(INPUT_DIR, fname)
            process_dxp(full, OUTPUT_DIR)

if __name__ == "__main__":
    main()
