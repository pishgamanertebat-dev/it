from pathlib import Path
import json
import re
import fitz

PDF = Path(r"E:\KomatsoAI\HD785-5\HD785-5 shop manual.pdf")
OUT = Path(r"E:\KomatsoAI\HD785-5\hd785_5_anchor_map.json")

ANCHORS = [('01', '01-1', 'contents'), ('01', '01-2', 'general_assembly'), ('01', '01-3', 'specifications'),
 ('01', '01-6', 'weight_table'), ('01', '01-7', 'lubricant_coolant'), ('10', '10-1', 'contents'),
 ('10', '10-4', 'radiator_tc_cooler'), ('10', '10-5', 'brake_oil_cooler'), ('10', '10-6', 'power_train'),
 ('10', '10-7', 'output_shaft'), ('10', '10-8', 'tc_transmission_piping'), ('10', '10-9', 'tc_transmission_circuit'),
 ('10', '10-10', 'tc_transmission_schematics'), ('10', '10-12', 'torque_converter'),
 ('10', '10-14', 'torque_converter_valve'), ('10', '10-18', 'lockup_solenoid'), ('10', '10-19', 'transmission'),
 ('10', '10-22', 'transmission_valve'), ('10', '10-23', 'ecmv'), ('10', '10-28', 'lubrication_relief'),
 ('10', '10-29', 'front_axle'), ('10', '10-30', 'rear_axle'), ('10', '10-32', 'differential'),
 ('10', '10-34', 'differential_lock'), ('10', '10-36', 'diff_lock_solenoid'), ('10', '10-37', 'final_drive'),
 ('10', '10-38', 'front_brake'), ('10', '10-39', 'rear_brake'), ('10', '10-41', 'parking_brake'),
 ('10', '10-42', 'wheels'), ('10', '10-42-2', 'axle_support'), ('10', '10-44', 'steering_system'),
 ('10', '10-45', 'steering_wheel'), ('10', '10-46', 'steering_linkage'), ('10', '10-48', 'suspension_system'),
 ('10', '10-50', 'suspension_cylinder'), ('10', '10-56', 'air_piping'), ('10', '10-58', 'air_circuit'),
 ('10', '10-59-1', 'desiccant_air_drier'), ('10', '10-60', 'air_governor'), ('10', '10-63', 'hand_brake_valve'),
 ('10', '10-64', 'brake_valve'), ('10', '10-65', 'emergency_brake_valve'), ('10', '10-68', 'parking_brake_control'),
 ('10', '10-69', 'parking_brake_pilot'), ('10', '10-70', 'relay_valve'), ('10', '10-71', 'parking_brake_relay'),
 ('10', '10-72', 'quick_release'), ('10', '10-73', 'ratio_valve'), ('10', '10-74', 'front_brake_chamber'),
 ('10', '10-75', 'rear_brake_chamber'), ('10', '10-78', 'slack_adjuster'), ('10', '10-79', 'air_drier'),
 ('10', '10-80', 'hydraulic_piping'), ('10', '10-82', 'hydraulic_circuit'), ('10', '10-83', 'dump_body_control'),
 ('10', '10-84', 'hydraulic_tank'), ('10', '10-85', 'bcv'), ('10', '10-86', 'steering_valve'),
 ('10', '10-90', 'demand_valve'), ('10', '10-94', 'hoist_valve'), ('10', '10-96', 'crossover_relief'),
 ('10', '10-97', 'steering_hoist_cylinders'), ('10', '10-99', 'electrical_circuit'),
 ('10', '10-106', 'air_conditioner'), ('10', '10-113', 'engine_control'), ('10', '10-122', 'automatic_shift'),
 ('10', '10-125', 'transmission_controller'), ('10', '10-135', 'self_diagnostic'),
 ('10', '10-136', 'emergency_steering'), ('10', '10-141', 'automatic_suspension'),
 ('10', '10-148', 'payload_meter_printer'), ('10', '10-158', 'payload_meter_card'), ('10', '10-198', 'machine_monitor'),
 ('10', '10-215', 'pmc'), ('10', '10-228-1', 'maintenance_monitor'), ('10', '10-229', 'abs_asr'),
 ('10', '10-247', 'asr_ii'), ('10', '10-252', 'hydraulic_tank_pressurizing'), ('10', '10-256', 'auto_greasing'),
 ('20A', '20-1', 'contents'), ('20A', '20-2', 'standard_engine'), ('20A', '20-3', 'standard_chassis'),
 ('20A', '20-8', 'standard_electrical'), ('20A', '20-101', 'testing_adjusting'), ('20A', '20-102', 'tools'),
 ('20A', '20-104', 'valve_clearance'), ('20A', '20-105', 'compression'), ('20A', '20-106', 'blowby'),
 ('20A', '20-107', 'boost'), ('20A', '20-108', 'exhaust_temp'), ('20A', '20-109', 'injection_timing'),
 ('20A', '20-114', 'accelerator_pedal'), ('20A', '20-115', 'tc_valve_pressure'), ('20A', '20-115-2', 'ecmv_pressure'),
 ('20A', '20-118', 'transmission_lube_pressure'), ('20A', '20-119', 'stall_speed'), ('20A', '20-121', 'brake_pressure'),
 ('20A', '20-124', 'parking_brake'), ('20A', '20-125', 'brake_performance'), ('20A', '20-126', 'front_suspension'),
 ('20A', '20-130', 'rear_suspension'), ('20A', '20-134', 'suspension_air'), ('20A', '20-135', 'air_pressure'),
 ('20A', '20-135-1', 'air_drier_inspection'), ('20A', '20-138', 'steering_hoist_pressure'),
 ('20A', '20-141', 'electronic_monitor'), ('20A', '20-142', 'engine_controller_q'),
 ('20A', '20-143', 'transmission_controller_setting'), ('20A', '20-144', 'controller_memory_delete'),
 ('20A', '20-147', 'emergency_escape'), ('20A', '20-153', 'modulation_checker'),
 ('20A', '20-158', 'hydraulic_tank_pressure'), ('20A', '20-159', 'quick_pm'), ('20A', '20-163', 'pm_clinic'),
 ('20T', '20-201', 'troubleshooting'), ('20T', '20-202', 'points'), ('20T', '20-203', 'sequence'),
 ('20T', '20-204', 'maintenance_points'), ('20T', '20-212', 'checks_before'), ('20T', '20-214', 'connector_position'),
 ('20T', '20-220', 'connector_pin_arrangement'), ('20T', '20-226', 'connector_pin_table'),
 ('20T', '20-236', 'electrical_functions'), ('20T', '20-244', 'monitor_service_mode'),
 ('20T', '20-259', 'self_diagnostic_display'), ('20T', '20-266', 'service_codes'),
 ('20T', '20-276', 'judgement_tables'), ('20T', '20-277', 'troubleshooting_charts'), ('20T', '20-301', 'g_mode'),
 ('20T', '20-401', 's_mode'), ('20T', '20-501', 'a_mode'), ('20T', '20-601', 'sp_mode'), ('20T', '20-701', 'lp_mode'),
 ('20T', '20-801', 'lc_mode'), ('20T', '20-901', 'r_mode'), ('20T', '20-1001', 'h_mode'), ('20T', '20-1101', 'p_mode'),
 ('20T', '20-1201', 'm_mode'), ('20T', '20-1251', 'abs_asr'), ('20T', '20-1301', 'maintenance_monitor'),
 ('20T', '20-1501', 'vhms'), ('20T', '20-1502', 'vhms_precautions'), ('20T', '20-1505', 'vhms_overview'),
 ('20T', '20-1510', 'vhms_structure'), ('20T', '20-1514', 'vhms_troubleshooting'),
 ('20T', '20-1538', 'vhms_disassembly'), ('20T', '20-1543', 'vhms_setting'), ('20T', '20-1545', 'vhms_data'),
 ('20T', '20-1550', 'vhms_maintenance'), ('20T', '20-1551', 'vhms_parts'), ('20T', '20-1552', 'vhms_initial_setup'),
 ('30', '30-1', 'contents'), ('30', '30-3', 'method'), ('30', '30-4', 'precautions'), ('30', '30-6', 'special_tools'),
 ('30', '30-9', 'starting_motor'), ('30', '30-10', 'alternator'), ('30', '30-11', 'engine_oil_cooler'),
 ('30', '30-12', 'left_injection_pump'), ('30', '30-13', 'right_injection_pump'), ('30', '30-14', 'air_compressor'),
 ('30', '30-15', 'water_pump'), ('30', '30-16', 'aftercooler'), ('30', '30-18', 'turbocharger'),
 ('30', '30-19', 'nozzle_holder'), ('30', '30-20', 'cylinder_head'), ('30', '30-23', 'exhaust_brake'),
 ('30', '30-24', 'engine'), ('30', '30-29', 'front_seal'), ('30', '30-32', 'rear_seal'),
 ('30', '30-36', 'radiator_tc_cooler'), ('30', '30-38', 'brake_cooler'), ('30', '30-39', 'output_shaft'),
 ('30', '30-44', 'tc_transmission'), ('30', '30-47', 'centering'), ('30', '30-48', 'tc_control_valve'),
 ('30', '30-50', 'tc_transmission_connection'), ('30', '30-52', 'tc_pto'), ('30', '30-64', 'transmission'),
 ('30', '30-95', 'ecmv'), ('30', '30-97', 'differential'), ('30', '30-112', 'differential_lock'),
 ('30', '30-129', 'front_wheel'), ('30', '30-130', 'rear_wheel'), ('30', '30-132', 'front_brake_caliper'),
 ('30', '30-134', 'front_brake_pad'), ('30', '30-135', 'front_wheel_hub'), ('30', '30-140', 'final_drive_carrier'),
 ('30', '30-142', 'final_drive'), ('30', '30-146', 'rear_brake'), ('30', '30-147', 'rear_wheel_brake'),
 ('30', '30-150', 'parking_brake_pad'), ('30', '30-150-2', 'parking_brake_caliper'),
 ('30', '30-153', 'front_suspension'), ('30', '30-155-4', 'king_pin'), ('30', '30-156', 'variable_damping_valve'),
 ('30', '30-158', 'rear_suspension'), ('30', '30-161', 'air_governor'), ('30', '30-163', 'brake_valve'),
 ('30', '30-168', 'parking_brake_chamber'), ('30', '30-172', 'rear_brake_chamber'),
 ('30', '30-180', 'front_brake_chamber'), ('30', '30-184', 'slack_adjuster'), ('30', '30-185-1', 'air_drier'),
 ('30', '30-186', 'brake_cooling_pump'), ('30', '30-187', 'steering_hoist_pump'),
 ('30', '30-188', 'tc_transmission_pump'), ('30', '30-189', 'emergency_steering_pump'),
 ('30', '30-190', 'steering_valve'), ('30', '30-202', 'bcv'), ('30', '30-203', 'demand_valve'),
 ('30', '30-205', 'relief_valve'), ('30', '30-206', 'hoist_valve'), ('30', '30-210', 'steering_cylinder'),
 ('30', '30-216', 'hoist_cylinder'), ('30', '30-219', 'body'), ('30', '30-221', 'controller'),
 ('30', '30-222', 'ac_compressor'), ('30', '30-223', 'receiver_tank'), ('30', '30-224', 'ac_condenser'),
 ('40', '40-1', 'contents'), ('40', '40-2', 'output_shaft'), ('40', '40-3', 'tc_valve'), ('40', '40-4', 'tc'),
 ('40', '40-6', 'transmission'), ('40', '40-11', 'ecmv'), ('40', '40-12', 'front_axle'),
 ('40', '40-13-1', 'front_axle_abs_asr'), ('40', '40-14', 'rear_axle_support'), ('40', '40-15', 'differential'),
 ('40', '40-17', 'final_drive'), ('40', '40-18', 'front_brake'), ('40', '40-19', 'rear_brake'),
 ('40', '40-20-1', 'rear_brake_abs_asr'), ('40', '40-21', 'parking_brake'), ('40', '40-22', 'steering_linkage'),
 ('40', '40-24', 'front_suspension'), ('40', '40-25', 'rear_suspension'), ('40', '40-26', 'parking_brake_chamber'),
 ('40', '40-27', 'slack_adjuster'), ('40', '40-28', 'tc_transmission_pump'), ('40', '40-29', 'hydraulic_pumps'),
 ('40', '40-30', 'demand_crossover'), ('40', '40-31', 'hoist_valve'), ('40', '40-32', 'steering_cylinder'),
 ('40', '40-33', 'hoist_cylinder'), ('90', '90-1', 'contents'), ('90', '90-3', 'cab_mech_1'),
 ('90', '90-5', 'cab_mech_2'), ('90', '90-7', 'cab_mech_3'), ('90', '90-9', 'cab_elec_1'),
 ('90', '90-11', 'cab_elec_2'), ('90', '90-13', 'cab_elec_3'), ('90', '90-15', 'cab_elec_4'),
 ('90', '90-17', 'cab_elec_5'), ('90', '90-19', 'outside_cab_1'), ('90', '90-21', 'outside_cab_2'),
 ('90', '90-23', 'electrical_control'), ('90', '90-25', 'automatic_shift_1'), ('90', '90-27', 'automatic_shift_2'),
 ('90', '90-29', 'automatic_suspension_1'), ('90', '90-31', 'automatic_suspension_2'),
 ('90', '90-33', 'payload_printer'), ('90', '90-35', 'payload_card'), ('90', '90-37', 'pmc_1'),
 ('90', '90-39', 'pmc_2'), ('90', '90-41', 'asr_ii'), ('90', '90-43', 'air_circuit'),
 ('90', '90-201', 'engine_control'), ('90', '90-203', 'transmission_electronic'),
 ('90', '90-205', 'transmission_mechanical'), ('90', '90-207', 'vhms_electrical')]

# These section boundaries were established by the first diagnostic run.
# They deliberately exclude the global master-contents pages (PDF 3-8),
# which caused the false matches in V1.
BOUNDS = {
    "01":  (29, 36),
    "10":  (37, 318),
    "20A": (319, 408),
    "20T": (409, 1075),
    "30":  (1076, 1325),
    "40":  (1326, 1366),
    "90":  (1367, 1392),
}

# First real content page after each section's own contents pages.
CONTENT_FLOOR = {
    "01": 30,
    "10": 40,
    "20A": 320,
    "20T": 409,
    "30": 1078,
    "40": 1327,
    "90": 1368,
}

CONTENTS_START = {
    "01": 29,
    "10": 37,
    "20A": 319,
    "30": 1076,
    "40": 1326,
    "90": 1367,
}

def token_patterns(token):
    parts = token.split("-")
    core = r"\s*-\s*".join(re.escape(p) for p in parts)

    # Exact-ish standalone footer/header line.
    strict = re.compile(
        rf"^\s*(?:HD(?:785|985)-5\s+)?{core}(?:\s+HD(?:785|985)-5)?\s*$",
        re.I,
    )

    # Loose occurrence, but don't allow 20-115 to match 20-115-2.
    loose = re.compile(
        rf"(?<!\d){core}(?!\s*-\s*\d)(?!\d)",
        re.I,
    )
    return strict, loose

if not PDF.is_file():
    raise SystemExit(f"PDF not found: {PDF}")

with fitz.open(PDF) as doc:
    if doc.page_count != 1392:
        raise SystemExit(f"Unexpected PDF page count: {doc.page_count} (expected 1392)")

    page_lines = {}
    for p in range(1, doc.page_count + 1):
        text = doc.load_page(p - 1).get_text("text") or ""
        page_lines[p] = [" ".join(x.split()) for x in text.splitlines() if x.strip()]

    resolved = []
    unresolved = []
    previous_by_section = {}

    for section, internal, label in ANCHORS:
        # Section-contents anchors are already confirmed by the first diagnostic.
        if label == "contents" and section in CONTENTS_START:
            page = CONTENTS_START[section]
            resolved.append({
                "section": section, "internal": internal, "label": label,
                "pdf_start": page, "method": "confirmed_section_start"
            })
            previous_by_section[section] = page
            continue

        lo, hi = BOUNDS[section]
        lo = max(lo, CONTENT_FLOOR[section])
        strict_rx, loose_rx = token_patterns(internal)

        strict_pages = []
        edge_pages = []
        body_pages = []

        for p in range(lo, hi + 1):
            lines = page_lines[p]
            strict_hit = False
            edge_hit = False
            body_hit = False

            for idx, line in enumerate(lines):
                if strict_rx.match(line):
                    strict_hit = True
                    break

                if loose_rx.search(line):
                    if idx < 8 or idx >= max(0, len(lines) - 8):
                        edge_hit = True
                    else:
                        body_hit = True

            if strict_hit:
                strict_pages.append(p)
            elif edge_hit:
                edge_pages.append(p)
            elif body_hit:
                body_pages.append(p)

        method = None
        candidates = []

        if len(strict_pages) == 1:
            candidates = strict_pages
            method = "strict"
        elif len(strict_pages) > 1:
            candidates = strict_pages
            method = "strict_multiple"
        elif len(edge_pages) == 1:
            candidates = edge_pages
            method = "edge_loose"
        elif len(edge_pages) > 1:
            candidates = edge_pages
            method = "edge_multiple"
        elif len(body_pages) == 1:
            candidates = body_pages
            method = "body_loose"
        elif len(body_pages) > 1:
            candidates = body_pages
            method = "body_multiple"
        else:
            method = "missing"

        # Monotonic filtering can safely discard references to earlier pages.
        prev = previous_by_section.get(section)
        if len(candidates) > 1 and prev is not None:
            later = [p for p in candidates if p > prev]
            if len(later) == 1:
                candidates = later
                method += "_monotonic"

        if len(candidates) == 1:
            page = candidates[0]
            resolved.append({
                "section": section, "internal": internal, "label": label,
                "pdf_start": page, "method": method
            })
            previous_by_section[section] = page
        else:
            unresolved.append({
                "section": section,
                "internal": internal,
                "label": label,
                "strict": strict_pages,
                "edge": edge_pages,
                "body": body_pages,
                "previous_resolved_pdf": prev,
            })

    # Global sanity check for the resolved anchors in manual order.
    starts = [x["pdf_start"] for x in resolved]
    nondecreasing = all(b >= a for a, b in zip(starts, starts[1:]))

    payload = {
        "manual": str(PDF),
        "pdf_pages": doc.page_count,
        "anchor_count": len(ANCHORS),
        "resolved_count": len(resolved),
        "unresolved_count": len(unresolved),
        "section_bounds": BOUNDS,
        "resolved": resolved,
        "unresolved": unresolved,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("HD785-5 MAPPING V2")
    print(f"PDF pages       : {doc.page_count}")
    print(f"Anchors         : {len(ANCHORS)}")
    print(f"Resolved        : {len(resolved)}")
    print(f"Unresolved      : {len(unresolved)}")
    print(f"Order sane      : {nondecreasing}")
    print(f"Map file        : {OUT}")

    print("\nCONFIRMED SECTION STARTS")
    for sec, page in CONTENTS_START.items():
        print(f"{sec:3} -> PDF {page}")

    if unresolved:
        print("\nUNRESOLVED ONLY")
        for x in unresolved:
            print(
                f"{x['section']:3} | {x['internal']:10} | {x['label']:34} | "
                f"strict={x['strict']} edge={x['edge']} body={x['body']} "
                f"prev={x['previous_resolved_pdf']}"
            )
        print("\nSTATUS: REVIEW_NEEDED")
    else:
        print("\nSTATUS: CLEAN")
        print("Upload hd785_5_anchor_map.json; no full CMD dump is needed.")
