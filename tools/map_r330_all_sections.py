from pathlib import Path
import re
import fitz

PDF = Path(r"E:\KomatsoAI\R330LC-9S\R330LC-9S shop manual.pdf")

ANCHORS = [
    ("general", "safety_hints", "1-1", "Group 1 Safety Hints"),
    ("general", "specifications", "1-10", "Group 2 Specifications"),

    ("structure_function", "pump_device", "2-1", "Group 1 Pump Device"),
    ("structure_function", "main_control_valve", "2-20", "Group 2 Main Control Valve"),
    ("structure_function", "swing_device", "2-46", "Group 3 Swing Device"),
    ("structure_function", "travel_device", "2-57", "Group 4 Travel Device"),
    ("structure_function", "rcv_lever", "2-66", "Group 5 RCV Lever"),
    ("structure_function", "rcv_pedal", "2-73", "Group 6 RCV Pedal"),

    ("hydraulic_system", "hydraulic_circuit", "3-1", "Group 1 Hydraulic Circuit"),
    ("hydraulic_system", "main_circuit", "3-3", "Group 2 Main Circuit"),
    ("hydraulic_system", "pilot_circuit", "3-6", "Group 3 Pilot Circuit"),
    ("hydraulic_system", "single_operation", "3-14", "Group 4 Single Operation"),
    ("hydraulic_system", "combined_operation", "3-24", "Group 5 Combined Operation"),

    ("electrical_system", "component_location", "4-1", "Group 1 Component Location"),
    ("electrical_system", "electric_circuit", "4-3", "Group 2 Electric Circuit"),
    ("electrical_system", "electrical_component_specification", "4-38", "Group 3 Electrical Component Specification"),
    ("electrical_system", "connectors", "4-46", "Group 4 Connectors"),

    ("mechatronics_system", "outline", "5-1", "Group 1 Outline"),
    ("mechatronics_system", "mode_selection_system", "5-5", "Group 2 Mode Selection System"),
    ("mechatronics_system", "automatic_deceleration_system", "5-11", "Group 3 Automatic Deceleration System"),
    ("mechatronics_system", "power_boost_system", "5-13", "Group 4 Power Boost System"),
    ("mechatronics_system", "travel_speed_control_system", "5-15", "Group 5 Travel Speed Control System"),
    ("mechatronics_system", "automatic_warming_up_function", "5-17", "Group 6 Automatic Warming Up Function"),
    ("mechatronics_system", "engine_overheat_prevention_function", "5-19", "Group 7 Engine Overheat Prevention Function"),
    ("mechatronics_system", "variable_power_control_system", "5-21", "Group 8 Variable Power Control System"),
    ("mechatronics_system", "attachment_flow_control_system", "5-22", "Group 9 Attachment Flow Control System"),
    ("mechatronics_system", "anti_restart_system", "5-23", "Group 10 Anti-Restart System"),
    ("mechatronics_system", "self_diagnostic_system", "5-24", "Group 11 Self-Diagnostic System"),
    ("mechatronics_system", "engine_control_system", "5-31", "Group 12 Engine Control System"),
    ("mechatronics_system", "eppr_valve", "5-33", "Group 13 EPPR Valve"),
    ("mechatronics_system", "monitoring_system", "5-39", "Group 14 Monitoring System"),
    ("mechatronics_system", "fuel_warmer_system", "5-71", "Group 15 Fuel Warmer System"),

    ("troubleshooting", "before_troubleshooting", "6-1", "Group 1 Before Troubleshooting"),
    ("troubleshooting", "hydraulic_mechanical_system", "6-4", "Group 2 Hydraulic and Mechanical System"),
    ("troubleshooting", "electrical_system", "6-24", "Group 3 Electrical System"),
    ("troubleshooting", "mechatronics_system", "6-56", "Group 4 Mechatronics System"),

    ("maintenance_standard", "operational_performance_test", "7-1", "Group 1 Operational Performance Test"),
    ("maintenance_standard", "major_components", "7-23", "Group 2 Major Components"),
    ("maintenance_standard", "track_work_equipment", "7-32", "Group 3 Track and Work Equipment"),

    ("disassembly_assembly", "precaution", "8-1", "Group 1 Precaution"),
    ("disassembly_assembly", "tightening_torque", "8-4", "Group 2 Tightening Torque"),
    ("disassembly_assembly", "pump_device", "8-7", "Group 3 Pump Device"),
    ("disassembly_assembly", "main_control_valve", "8-30", "Group 4 Main Control Valve"),
    ("disassembly_assembly", "swing_device", "8-51", "Group 5 Swing Device"),
    ("disassembly_assembly", "travel_device", "8-79", "Group 6 Travel Device"),
    ("disassembly_assembly", "rcv_lever", "8-112", "Group 7 RCV Lever"),
    ("disassembly_assembly", "turning_joint", "8-126", "Group 8 Turning Joint"),
    ("disassembly_assembly", "boom_arm_bucket_cylinder", "8-131", "Group 9 Boom, Arm and Bucket Cylinder"),
    ("disassembly_assembly", "undercarriage", "8-148", "Group 10 Undercarriage"),
    ("disassembly_assembly", "work_equipment", "8-160", "Group 11 Work Equipment"),

    ("component_mounting_torque", "introduction_guide", "9-1", "Group 1 Introduction Guide"),
    ("component_mounting_torque", "engine_system", "9-2", "Group 2 Engine System"),
    ("component_mounting_torque", "electric_system", "9-4", "Group 3 Electric System"),
    ("component_mounting_torque", "hydraulic_system", "9-6", "Group 4 Hydraulic System"),
    ("component_mounting_torque", "undercarriage", "9-9", "Group 5 Undercarriage"),
    ("component_mounting_torque", "structure", "9-11", "Group 6 Structure"),
    ("component_mounting_torque", "work_equipment", "9-15", "Group 7 Work Equipment"),
]

def exact_internal_page_line(lines, token):
    a, b = token.split("-", 1)
    rx = re.compile(rf"^\s*{re.escape(a)}\s*-\s*{re.escape(b)}\s*$")
    return any(rx.match(line) for line in lines)

if not PDF.is_file():
    raise SystemExit(f"PDF not found: {PDF}")

with fitz.open(PDF) as doc:
    page_lines = []
    for i in range(doc.page_count):
        text = doc.load_page(i).get_text("text") or ""
        lines = [x.strip() for x in text.splitlines() if x.strip()]
        page_lines.append(lines)

    print(f"PDF: {PDF}")
    print(f"PDF pages: {doc.page_count}")
    print(f"Anchors: {len(ANCHORS)}")
    print()

    results = []
    missing = 0
    multiple = 0

    for section, key, internal, title in ANCHORS:
        candidates = [
            i + 1
            for i, lines in enumerate(page_lines)
            if exact_internal_page_line(lines, internal)
        ]

        if len(candidates) == 1:
            chosen = candidates[0]
        elif len(candidates) == 0:
            chosen = None
            missing += 1
        else:
            chosen = candidates[0]
            multiple += 1

        results.append(
            {
                "section": section,
                "key": key,
                "internal": internal,
                "title": title,
                "candidates": candidates,
                "pdf_start": chosen,
            }
        )

        print(
            f"{section:28} | {key:36} | internal={internal:6} | "
            f"PDF_START={str(chosen):>7} | candidates={candidates}"
        )

    print("\n" + "=" * 112)
    print("PROPOSED RANGES")
    print("=" * 112)

    valid_starts = [r["pdf_start"] for r in results if r["pdf_start"] is not None]
    monotonic = all(b > a for a, b in zip(valid_starts, valid_starts[1:]))

    for idx, row in enumerate(results):
        start = row["pdf_start"]
        if start is None:
            end = None
        else:
            next_start = None
            for later in results[idx + 1:]:
                if later["pdf_start"] is not None:
                    next_start = later["pdf_start"]
                    break
            end = (next_start - 1) if next_start is not None else doc.page_count

        print(
            f"{row['section']}.{row['key']} | "
            f"internal={row['internal']} | "
            f"PDF={start if start is not None else 'MISSING'}"
            f"-{end if end is not None else 'MISSING'} | "
            f"{row['title']}"
        )

    print("\nSUMMARY")
    print(f"Missing anchors   : {missing}")
    print(f"Multiple matches  : {multiple}")
    print(f"Strictly monotonic: {monotonic}")

    if missing == 0 and multiple == 0 and monotonic:
        print("\nSTATUS: CLEAN")
        print("All 57 internal start-page labels mapped uniquely and in order.")
    else:
        print("\nSTATUS: REVIEW_NEEDED")
        print("Send this complete output back; only ambiguous/missing entries need manual review.")
