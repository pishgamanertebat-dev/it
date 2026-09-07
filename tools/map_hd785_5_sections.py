from pathlib import Path
import re
import fitz

PDF = Path(r"E:\KomatsoAI\HD785-5\HD785-5 shop manual.pdf")

# (section, internal-page, routing label)
ANCHORS = [
# 01 GENERAL
("01","01-1","contents"),("01","01-2","general_assembly"),("01","01-3","specifications"),
("01","01-6","weight_table"),("01","01-7","lubricant_coolant"),

# 10 STRUCTURE AND FUNCTION
("10","10-1","contents"),("10","10-4","radiator_tc_cooler"),("10","10-5","brake_oil_cooler"),
("10","10-6","power_train"),("10","10-7","output_shaft"),("10","10-8","tc_transmission_piping"),
("10","10-9","tc_transmission_circuit"),("10","10-10","tc_transmission_schematics"),
("10","10-12","torque_converter"),("10","10-14","torque_converter_valve"),("10","10-18","lockup_solenoid"),
("10","10-19","transmission"),("10","10-22","transmission_valve"),("10","10-23","ecmv"),
("10","10-28","lubrication_relief"),("10","10-29","front_axle"),("10","10-30","rear_axle"),
("10","10-32","differential"),("10","10-34","differential_lock"),("10","10-36","diff_lock_solenoid"),
("10","10-37","final_drive"),("10","10-38","front_brake"),("10","10-39","rear_brake"),
("10","10-41","parking_brake"),("10","10-42","wheels"),("10","10-42-2","axle_support"),
("10","10-44","steering_system"),("10","10-45","steering_wheel"),("10","10-46","steering_linkage"),
("10","10-48","suspension_system"),("10","10-50","suspension_cylinder"),("10","10-56","air_piping"),
("10","10-58","air_circuit"),("10","10-59-1","desiccant_air_drier"),("10","10-60","air_governor"),
("10","10-63","hand_brake_valve"),("10","10-64","brake_valve"),("10","10-65","emergency_brake_valve"),
("10","10-68","parking_brake_control"),("10","10-69","parking_brake_pilot"),("10","10-70","relay_valve"),
("10","10-71","parking_brake_relay"),("10","10-72","quick_release"),("10","10-73","ratio_valve"),
("10","10-74","front_brake_chamber"),("10","10-75","rear_brake_chamber"),("10","10-78","slack_adjuster"),
("10","10-79","air_drier"),("10","10-80","hydraulic_piping"),("10","10-82","hydraulic_circuit"),
("10","10-83","dump_body_control"),("10","10-84","hydraulic_tank"),("10","10-85","bcv"),
("10","10-86","steering_valve"),("10","10-90","demand_valve"),("10","10-94","hoist_valve"),
("10","10-96","crossover_relief"),("10","10-97","steering_hoist_cylinders"),("10","10-99","electrical_circuit"),
("10","10-106","air_conditioner"),("10","10-113","engine_control"),("10","10-122","automatic_shift"),
("10","10-125","transmission_controller"),("10","10-135","self_diagnostic"),("10","10-136","emergency_steering"),
("10","10-141","automatic_suspension"),("10","10-148","payload_meter_printer"),
("10","10-158","payload_meter_card"),("10","10-198","machine_monitor"),("10","10-215","pmc"),
("10","10-228-1","maintenance_monitor"),("10","10-229","abs_asr"),("10","10-247","asr_ii"),
("10","10-252","hydraulic_tank_pressurizing"),("10","10-256","auto_greasing"),

# 20 TESTING / ADJUSTING
("20A","20-1","contents"),("20A","20-2","standard_engine"),("20A","20-3","standard_chassis"),
("20A","20-8","standard_electrical"),("20A","20-101","testing_adjusting"),("20A","20-102","tools"),
("20A","20-104","valve_clearance"),("20A","20-105","compression"),("20A","20-106","blowby"),
("20A","20-107","boost"),("20A","20-108","exhaust_temp"),("20A","20-109","injection_timing"),
("20A","20-114","accelerator_pedal"),("20A","20-115","tc_valve_pressure"),("20A","20-115-2","ecmv_pressure"),
("20A","20-118","transmission_lube_pressure"),("20A","20-119","stall_speed"),("20A","20-121","brake_pressure"),
("20A","20-124","parking_brake"),("20A","20-125","brake_performance"),("20A","20-126","front_suspension"),
("20A","20-130","rear_suspension"),("20A","20-134","suspension_air"),("20A","20-135","air_pressure"),
("20A","20-135-1","air_drier_inspection"),("20A","20-138","steering_hoist_pressure"),
("20A","20-141","electronic_monitor"),("20A","20-142","engine_controller_q"),
("20A","20-143","transmission_controller_setting"),("20A","20-144","controller_memory_delete"),
("20A","20-147","emergency_escape"),("20A","20-153","modulation_checker"),
("20A","20-158","hydraulic_tank_pressure"),("20A","20-159","quick_pm"),("20A","20-163","pm_clinic"),

# 20 TROUBLESHOOTING
("20T","20-201","troubleshooting"),("20T","20-202","points"),("20T","20-203","sequence"),
("20T","20-204","maintenance_points"),("20T","20-212","checks_before"),("20T","20-214","connector_position"),
("20T","20-220","connector_pin_arrangement"),("20T","20-226","connector_pin_table"),
("20T","20-236","electrical_functions"),("20T","20-244","monitor_service_mode"),
("20T","20-259","self_diagnostic_display"),("20T","20-266","service_codes"),
("20T","20-276","judgement_tables"),("20T","20-277","troubleshooting_charts"),
("20T","20-301","g_mode"),("20T","20-401","s_mode"),("20T","20-501","a_mode"),
("20T","20-601","sp_mode"),("20T","20-701","lp_mode"),("20T","20-801","lc_mode"),
("20T","20-901","r_mode"),("20T","20-1001","h_mode"),("20T","20-1101","p_mode"),
("20T","20-1201","m_mode"),("20T","20-1251","abs_asr"),("20T","20-1301","maintenance_monitor"),
("20T","20-1501","vhms"),("20T","20-1502","vhms_precautions"),("20T","20-1505","vhms_overview"),
("20T","20-1510","vhms_structure"),("20T","20-1514","vhms_troubleshooting"),
("20T","20-1538","vhms_disassembly"),("20T","20-1543","vhms_setting"),
("20T","20-1545","vhms_data"),("20T","20-1550","vhms_maintenance"),
("20T","20-1551","vhms_parts"),("20T","20-1552","vhms_initial_setup"),

# 30 DISASSEMBLY AND ASSEMBLY
("30","30-1","contents"),("30","30-3","method"),("30","30-4","precautions"),("30","30-6","special_tools"),
("30","30-9","starting_motor"),("30","30-10","alternator"),("30","30-11","engine_oil_cooler"),
("30","30-12","left_injection_pump"),("30","30-13","right_injection_pump"),("30","30-14","air_compressor"),
("30","30-15","water_pump"),("30","30-16","aftercooler"),("30","30-18","turbocharger"),
("30","30-19","nozzle_holder"),("30","30-20","cylinder_head"),("30","30-23","exhaust_brake"),
("30","30-24","engine"),("30","30-29","front_seal"),("30","30-32","rear_seal"),
("30","30-36","radiator_tc_cooler"),("30","30-38","brake_cooler"),("30","30-39","output_shaft"),
("30","30-44","tc_transmission"),("30","30-47","centering"),("30","30-48","tc_control_valve"),
("30","30-50","tc_transmission_connection"),("30","30-52","tc_pto"),("30","30-64","transmission"),
("30","30-95","ecmv"),("30","30-97","differential"),("30","30-112","differential_lock"),
("30","30-129","front_wheel"),("30","30-130","rear_wheel"),("30","30-132","front_brake_caliper"),
("30","30-134","front_brake_pad"),("30","30-135","front_wheel_hub"),("30","30-140","final_drive_carrier"),
("30","30-142","final_drive"),("30","30-146","rear_brake"),("30","30-147","rear_wheel_brake"),
("30","30-150","parking_brake_pad"),("30","30-150-2","parking_brake_caliper"),
("30","30-153","front_suspension"),("30","30-155-4","king_pin"),
("30","30-156","variable_damping_valve"),("30","30-158","rear_suspension"),
("30","30-161","air_governor"),("30","30-163","brake_valve"),("30","30-168","parking_brake_chamber"),
("30","30-172","rear_brake_chamber"),("30","30-180","front_brake_chamber"),("30","30-184","slack_adjuster"),
("30","30-185-1","air_drier"),("30","30-186","brake_cooling_pump"),("30","30-187","steering_hoist_pump"),
("30","30-188","tc_transmission_pump"),("30","30-189","emergency_steering_pump"),
("30","30-190","steering_valve"),("30","30-202","bcv"),("30","30-203","demand_valve"),
("30","30-205","relief_valve"),("30","30-206","hoist_valve"),("30","30-210","steering_cylinder"),
("30","30-216","hoist_cylinder"),("30","30-219","body"),("30","30-221","controller"),
("30","30-222","ac_compressor"),("30","30-223","receiver_tank"),("30","30-224","ac_condenser"),

# 40 MAINTENANCE STANDARD
("40","40-1","contents"),("40","40-2","output_shaft"),("40","40-3","tc_valve"),("40","40-4","tc"),
("40","40-6","transmission"),("40","40-11","ecmv"),("40","40-12","front_axle"),
("40","40-13-1","front_axle_abs_asr"),("40","40-14","rear_axle_support"),("40","40-15","differential"),
("40","40-17","final_drive"),("40","40-18","front_brake"),("40","40-19","rear_brake"),
("40","40-20-1","rear_brake_abs_asr"),("40","40-21","parking_brake"),("40","40-22","steering_linkage"),
("40","40-24","front_suspension"),("40","40-25","rear_suspension"),("40","40-26","parking_brake_chamber"),
("40","40-27","slack_adjuster"),("40","40-28","tc_transmission_pump"),("40","40-29","hydraulic_pumps"),
("40","40-30","demand_crossover"),("40","40-31","hoist_valve"),("40","40-32","steering_cylinder"),
("40","40-33","hoist_cylinder"),

# 90 OTHERS
("90","90-1","contents"),("90","90-3","cab_mech_1"),("90","90-5","cab_mech_2"),("90","90-7","cab_mech_3"),
("90","90-9","cab_elec_1"),("90","90-11","cab_elec_2"),("90","90-13","cab_elec_3"),
("90","90-15","cab_elec_4"),("90","90-17","cab_elec_5"),("90","90-19","outside_cab_1"),
("90","90-21","outside_cab_2"),("90","90-23","electrical_control"),("90","90-25","automatic_shift_1"),
("90","90-27","automatic_shift_2"),("90","90-29","automatic_suspension_1"),
("90","90-31","automatic_suspension_2"),("90","90-33","payload_printer"),("90","90-35","payload_card"),
("90","90-37","pmc_1"),("90","90-39","pmc_2"),("90","90-41","asr_ii"),("90","90-43","air_circuit"),
("90","90-201","engine_control"),("90","90-203","transmission_electronic"),
("90","90-205","transmission_mechanical"),("90","90-207","vhms_electrical"),
]

def token_rx(token):
    e = re.escape(token).replace(r"\-", r"\s*-\s*")
    # Only accept a page-number line/footer/header, not TOC item text.
    return re.compile(
        rf"^\s*(?:HD(?:785|985)-5\s+)?{e}(?:\s+HD(?:785|985)-5)?\s*$",
        re.I,
    )

if not PDF.is_file():
    raise SystemExit(f"PDF not found: {PDF}")

with fitz.open(PDF) as doc:
    print(f"PDF: {PDF}")
    print(f"PDF pages: {doc.page_count}")
    print(f"Anchors: {len(ANCHORS)}\n")

    pages = []
    for i in range(doc.page_count):
        text = doc.load_page(i).get_text("text") or ""
        pages.append([" ".join(x.split()) for x in text.splitlines() if x.strip()])

    results = []
    missing = multiple = 0

    for section, internal, label in ANCHORS:
        rx = token_rx(internal)
        cand = [i + 1 for i, lines in enumerate(pages) if any(rx.match(x) for x in lines)]
        chosen = cand[0] if len(cand) == 1 else None
        missing += (len(cand) == 0)
        multiple += (len(cand) > 1)
        results.append((section, internal, label, chosen, cand))
        print(f"{section:3} | {internal:10} | {label:34} | PDF_START={str(chosen):>5} | candidates={cand}")

    unique = [r for r in results if r[3] is not None]
    monotonic = all(b[3] > a[3] for a, b in zip(unique, unique[1:]))

    print("\n" + "=" * 95)
    print("SECTION STARTS / CONTENTS")
    print("=" * 95)
    for section, internal, label, chosen, cand in results:
        if label == "contents":
            print(f"{section:3} | {internal:8} | PDF={chosen} | candidates={cand}")

    print("\nSUMMARY")
    print(f"Missing anchors   : {missing}")
    print(f"Multiple matches  : {multiple}")
    print(f"Strictly monotonic: {monotonic}")

    if missing == 0 and multiple == 0 and monotonic:
        print("\nSTATUS: CLEAN")
    else:
        print("\nSTATUS: REVIEW_NEEDED")
        print("Send the COMPLETE output back. We will inspect only missing/multiple cases.")
