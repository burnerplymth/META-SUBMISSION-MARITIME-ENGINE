#!/usr/bin/env python3
"""Build the three replay datasets from the sourced replay timelines in this file.

Deterministic: json.dump(sort_keys=True, indent=2) + trailing newline; no
timestamps of build time. Every observed_steps entry converts exactly one
dated row of those timelines. `observed_delay_d` is the observed delay
where the source states one; 0.0 where the observed effect is not a delay
(rate/queue/capacity effect), and `delay_basis` records which case applies —
modeled midpoints are labelled "modeled_midpoint_of_observed_range", never
passed off as raw observations. All node_ids resolve to graph.json nodes
(asserted at build time against data/graph/graph.json).

Every disruption spec carries a non-null
`capacity_loss` in (0, 1] plus a `capacity_loss_basis` object
{kind, source, derivation} with kind in {"sourced", "derived", "modeled"}:
"sourced" = the loss figure is stated by the cited source; "derived" =
arithmetic only on sourced figures (no conversion factors); "modeled" = an
explicit modelling assumption whose inputs are sourced but whose mapping
to a capacity-loss fraction is ours — a modeled value is never presented
as sourced, and scorecards must print it next to that dataset's results.
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GRAPH = HERE.parent / "graph" / "graph.json"

def step(node, date, delay, basis, effect, source):
    return {"node_id": node, "observed_delay_d": delay, "delay_basis": basis,
            "effect": effect, "evidence_source": source, "evidence_date": date}

RED_SEA = {
    "id": "red_sea_2024",
    "title": "Red Sea / Bab el-Mandeb crisis, 2023-2025",
    "source_document": "curated timeline, section 1 (retrieved 2026-10-20)",
    "disruption": {"node_id": "chk_babelmandeb", "kind": "capacity_cut", "severity": 5,
                   "capacity_loss": 0.7,
                   "capacity_loss_basis": {
                       "kind": "sourced",
                       "source": "UNCTAD, via Africa Ports feature (Apr 2024): https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/",
                       "derivation": "The loss figure is stated by the source: gross tonnage of ships entering the Gulf of Aden fell >70% between the 1st half of Dec 2023 and the 1st half of Feb 2024; taken as 0.7 (the stated floor). Corroborated in the same timeline by Suez transits -70% by Jun 2024 (UNCTAD Review of Maritime Transport 2024). No arithmetic conversion applied. Container-ship capacity fell further (-92% by Jun 2024); 0.7 is the conservative all-shipping figure."},
                   "started_at": "2023-11-19T00:00:00Z",
                   "expected_duration_d": 559},
    "disruption_notes": "capacity_loss 0.7 = observed gross-tonnage fall through the Gulf of Aden (>70% by mid-Feb 2024, UNCTAD); container-ship capacity fell further (-92% by Jun 2024). expected_duration_d 559 = days from 2023-11-19 to the last timeline observation (2025-05-31, Suez tonnage still ~70% below 2023) - an observed lower bound, not a forecast; the disruption had not resolved in the source timeline.",
    "observed_steps": [
        step("chk_babelmandeb", "2023-11-19", 0.0, "not_a_delay_effect",
             "Incident involving the car carrier Galaxy Leader in the Red Sea - first of a series of incidents that led carriers to avoid the corridor (crew held until 22 Jan 2025).",
             "AI Timeline compilation of the crisis chronology: https://aitimeline.in/red-sea-shipping-attacks-rerouting-costs-timeline-14731/"),
        step("chk_suez", "2023-12-01", 0.0, "not_a_delay_effect",
             "Onset of sustained carrier diversions away from the Red Sea corridor (UNCTAD reference timeline).",
             "UNCTAD via Warconomy reference timeline: https://warconomy.com/chokepoints/red-sea-shipping/economic-impact"),
        step("chk_suez", "2023-12-31", 0.0, "not_a_delay_effect",
             "By end of December 2023, traffic through the Suez Canal was down by about half vs pre-crisis levels.",
             "UNCTAD Review of Maritime Transport 2024, via Maritime Executive: https://maritime-executive.com/article/unctad-maritime-chokepoints-are-trade-s-biggest-vulnerability"),
        step("sea_rotterdam", "2024-02-01", 10.0, "modeled_from_distance",
             "Spot container rates roughly doubled on Shanghai-Rotterdam between 1 Dec 2023 and 1 Feb 2024 (window end date). observed_delay_d is the MODELED one-way transit added by the Cape reroute (census: bypass +~3,000-6,000 nm), not a figure stated by the source.",
             "UNCTAD, via Africa Ports feature (Apr 2024): https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/"),
        step("chk_babelmandeb", "2024-02-15", 0.0, "not_a_delay_effect",
             "Gross tonnage of ships entering the Gulf of Aden fell >70% (1st half Dec 2023 to 1st half Feb 2024 window).",
             "UNCTAD, via Africa Ports feature (Apr 2024): https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/"),
        step("chk_capeofgoodhope", "2024-03-01", 10.0, "modeled_from_distance",
             "Cape of Good Hope arrivals up +85% gross tonnage (7-day moving average, first week of Mar 2024) vs first half of Dec 2023; containership arrivals at the Cape up +328%. observed_delay_d is the MODELED one-way transit added by the Cape reroute, not a figure stated by the source.",
             "UNCTAD based on Clarksons Research, via Africa Ports: https://africaports.co.za/2024/04/24/feature-red-sea-crisis-and-implications-for-trade-facilitation-in-africa/"),
        step("chk_suez", "2024-06-30", 0.0, "not_a_delay_effect",
             "Suez transits -70% vs mid-Dec 2023 (by Jun 2024). Canal revenue fell $9.4B (FY2022/23) to $7.2B (FY2023/24); transits 25,911 to 20,148.",
             "UNCTAD Review of Maritime Transport 2024, via AI Timeline summary: https://aitimeline.in/red-sea-shipping-attacks-rerouting-costs-timeline-14731/"),
        step("chk_capeofgoodhope", "2024-06-30", 10.0, "modeled_from_distance",
             "Cape of Good Hope arrivals +89% (by Jun 2024). Median Asia-Europe round trip cost approx $1.0M via Suez vs $1.7M via the Cape. observed_delay_d is the MODELED one-way transit added by the Cape reroute, not a figure stated by the source.",
             "UNCTAD Review of Maritime Transport 2024, via AI Timeline summary: https://aitimeline.in/red-sea-shipping-attacks-rerouting-costs-timeline-14731/"),
        step("chk_babelmandeb", "2024-06-30", 0.0, "not_a_delay_effect",
             "Container-ship capacity entering the Gulf of Aden -92% (by Jun 2024). Rerouting raised overall vessel demand by ~3% and container-ship demand by ~12% (UNCTAD via Splash247).",
             "UNCTAD Review of Maritime Transport 2024, via AI Timeline summary: https://aitimeline.in/red-sea-shipping-attacks-rerouting-costs-timeline-14731/"),
        step("sea_shanghai", "2024-12-31", 0.0, "not_a_delay_effect",
             "Shanghai Containerized Freight Index (SCFI) averaged 2,496 points in 2024, +149% year on year (rate channel; node is the index's home port).",
             "UNCTAD Review of Maritime Transport 2025, via Lift Europe: https://lift-europe.com/blog-detail/un-global-shipping-faces-weak-growth-2025-rerouting-costs-and-new-rules-reshape-trade"),
        step("chk_suez", "2025-05-31", 0.0, "not_a_delay_effect",
             "Suez tonnage still approximately 70% below its 2023 level - no full recovery 17 months in.",
             "UNCTAD, via Splash247: https://splash247.com/shippings-answer-to-geopolitics-go-around/"),
    ],
    "unresolved_timeline_nodes": "sea_jeddah and sea_portsaid are named scenario nodes in the curated timeline but have no dated observed effect of their own in the source table; no step was fabricated for them.",
}

PANAMA = {
    "id": "panama_2023_24",
    "title": "Panama Canal drought, 2023-24",
    "source_document": "curated timeline, section 2 (retrieved 2026-10-20)",
    "disruption": {"node_id": "chk_panama", "kind": "capacity_cut", "severity": 4,
                   "capacity_loss": 0.5,
                   "capacity_loss_basis": {
                       "kind": "derived",
                       "source": "Panama Canal Authority (ACP) Customer Advisory 30 Oct 2023, via DB Group: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought",
                       "derivation": "Derived by arithmetic only on the two sourced ACP slot counts: normal schedule ~36 transits/day, crisis minimum 18/day from 1 Feb 2024; capacity_loss = 1 - 18/36 = 0.5. No conversion factor involved."},
                   "started_at": "2023-10-30T00:00:00Z",
                   "expected_duration_d": 215},
    "disruption_notes": "capacity_loss 0.5 = ACP slot schedule 36/day normal to 18/day minimum (Feb 2024). expected_duration_d 215 = 2023-10-30 (ACP advisory) to 2024-06-01 (slots restored to 32/day, ~89% of normal). Queue/draft figures rest partly on the Ballast Markets secondary compilation of ACP/press reporting - re-verification against ACP advisories and IMF PortWatch is an open item.",
    "observed_steps": [
        step("chk_panama", "2023-10-25", 0.0, "not_a_delay_effect",
             "~135 ships waiting to transit (~50% above the normal 80-85).",
             "Ballast Markets compilation of ACP/press reporting: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_panama", "2023-10-30", 0.0, "not_a_delay_effect",
             "ACP advisory sets the progressive slot-reduction schedule: 24/day (7-30 Nov 2023), 22/day (Dec 2023), 20/day (Jan 2024), 18/day (from 1 Feb 2024), vs a normal ~36/day.",
             "ACP Customer Advisory 30 Oct 2023, via DB Group: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought"),
        step("chk_panama", "2023-11-01", 0.0, "not_a_delay_effect",
             "Slot-auction premiums average ~$2.5M per slot (range $1.5-4M) for 2-4 auctioned slots/day (Nov-Feb peak window; cost channel).",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_panama", "2023-11-30", 0.0, "not_a_delay_effect",
             "Neopanamax maximum draft limited to 44 ft (from a normal ~50 ft), forcing light-loading of the largest container ships and LNG carriers.",
             "DB Group summary of ACP measures: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought"),
        step("chk_panama", "2024-01-01", 0.0, "not_a_delay_effect",
             "Gatun Lake at 79.5 ft - lowest January level on record, ~6 ft below normal.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_panama", "2024-02-01", 0.0, "not_a_delay_effect",
             "Daily transit slots reach the crisis minimum: 18/day (a ~50% capacity cut).",
             "ACP schedule via DB Group: https://www.dbgroup.net/en/news/panama-canal-daily-transit-limited-due-drought"),
        step("chk_panama", "2024-02-29", 17.5, "modeled_midpoint_of_observed_range",
             "Queues of 135-150 ships; average waits 15-20 days, some vessels 25+ days (Nov 2023-Feb 2024 peak window). observed_delay_d 17.5 is the midpoint of the observed 15-20 d wait range.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("sea_newyork", "2024-02-29", 8.5, "modeled_midpoint_of_observed_range",
             "Asia to US East Coast container services rerouted via Suez (+7-10 days) during the peak-crisis window. observed_delay_d 8.5 is the midpoint of the observed +7-10 d range.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_suez", "2024-02-29", 8.5, "modeled_midpoint_of_observed_range",
             "Suez used as the substitute route for Asia-US East Coast services (+7-10 days) - while Suez itself was constrained by the Red Sea crisis from Dec 2023. observed_delay_d is the midpoint of the observed range.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("sea_losangeles", "2024-02-29", 0.0, "not_a_delay_effect",
             "US West Coast + rail land bridge used as a substitute for Asia-US East Coast flows during the peak-crisis window (substitution observed; no added-delay figure stated by the source). Some bulk carriers rerouted via Cape Horn (+18-21 days) - no graph node represents that leg.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_panama", "2024-03-01", 0.0, "not_a_delay_effect",
             "Recovery begins: slots raised to 22/day (Mar), then 24 (1 Apr), 27 (1 May).",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
        step("chk_panama", "2024-06-01", 6.0, "modeled_midpoint_of_observed_range",
             "Slots restored to 32/day (~89% of normal); Gatun Lake back to ~85.2 ft by 15 Jun; queues near normal (~88 ships in May, waits 5-7 days). observed_delay_d 6.0 is the midpoint of the observed 5-7 d wait range.",
             "Ballast Markets compilation: https://content.ballastmarkets.com/blog/2025-05-05-panama-canal-drought-nature-disrupts-trade/"),
    ],
    "unresolved_timeline_nodes": "sea_colon, sea_savannah and sea_houston are named scenario nodes in the curated timeline; the dated rows name sea_houston only inside the joint Asia-US East Coast rerouting row, which was converted once (sea_newyork step) to avoid triple-counting one observation. No separate dated effect exists for sea_colon or sea_savannah in the source table.",
}

LA = {
    "id": "la_2021",
    "title": "Los Angeles / Long Beach congestion, 2021",
    "source_document": "curated timeline, section 3 (retrieved 2026-10-20)",
    "disruption": {"node_id": "sea_losangeles", "kind": "congestion", "severity": 4,
                   "capacity_loss": 0.78,
                   "capacity_loss_basis": {
                       "kind": "modeled",
                       "source": "Marine Exchange of Southern California (MESocal) 10 Nov 2021 report, via Western Overseas: https://www.westernoverseas.com/port-congestion-update-11-12-21/ (same report already cited for the 2021-11-10 observed steps in this dataset)",
                       "derivation": "MODELED assumption - not a sourced figure and never to be presented as one. No sourced capacity-loss fraction exists for LA 2021: the source record quantifies queues and waits, not capacity loss, and berth throughput did not collapse (the Port of Los Angeles was on track for a record ~10.7M TEU in 2021, Port of Los Angeles release, 20 Dec 2021), so no throughput-drop derivation is defensible. Model: at the 10 Nov 2021 MESocal snapshot the complex held 109 container ships waiting and 30 at berth; capacity_loss is set to the waiting share of container ships present, 109 / (109 + 30) = 0.78 (rounded to 2 dp) - a stock-share proxy for the share of container-ship flow the berth/landside system was not serving at the peak snapshot, NOT a measured throughput drop. The binding constraint was landside (dwell/driver channel), not berth capacity. Any scorecard printing LA results must print this modeled assumption alongside them. (An earlier version carried capacity_loss as null; the engine then silently substituted its 0.5 default for congestion-kind specs.)"},
                   "started_at": "2021-09-19T00:00:00Z",
                   "expected_duration_d": 58},
    "disruption_notes": "capacity_loss 0.78 is a MODELED value (see capacity_loss_basis in the disruption spec for the full derivation): the source timeline quantifies queue counts and waits, not a capacity-loss fraction, and no sourced figure is claimed. The binding constraint was landside (dwell/driver channel), not berth capacity. expected_duration_d 58 = 2021-09-19 (first dated record in the timeline) to 2021-11-16 (offshore queuing system takes effect) - the window covered by the source timeline, not the full historical episode. Pre-pandemic baseline: the complex had never seen a backlog greater than 17 ships (MESocal).",
    "observed_steps": [
        step("sea_losangeles", "2021-09-19", 0.0, "not_a_delay_effect",
             "Record 97 vessels of all types in port (within 40 miles) as the queue builds (joint LA/LB complex figure).",
             "Marine Exchange of Southern California (MESocal) noon report via DredgeWire: https://dredgewire.com/vessel-congestion-outside-the-ports-of-los-angeles-and-long-beach-has-worsened-as-record-100-vessels-anchored/"),
        step("sea_longbeach", "2021-09-20", 0.0, "not_a_delay_effect",
             "Record 29 container ships in holding (drift) areas - anchorages full; 70+ container ships at anchor by 21 Sep, with 30+ at berth (joint LA/LB complex figures).",
             "MESocal via Seatrade Maritime: https://www.seatrade-maritime.com/ports-logistics/vessels-waiting-at-la-long-beach-ports-back-at-record-levels"),
        step("sea_losangeles", "2021-10-13", 0.0, "not_a_delay_effect",
             "White House brokers shift to 24/7 gate operations at LA/LB to address the backlog.",
             "AP via Manufacturing.net: https://www.manufacturing.net/supply-chain/news/21808634/la-long-beach-ports-will-issue-fines-for-backlogged-cargo"),
        step("sea_losangeles", "2021-10-18", 0.0, "not_a_delay_effect",
             "100 vessels at anchor (record), 157 vessels of all types in port, 97 of them container ships (joint LA/LB complex figures).",
             "MESocal noon report 18 Oct via DredgeWire: https://dredgewire.com/vessel-congestion-outside-the-ports-of-los-angeles-and-long-beach-has-worsened-as-record-100-vessels-anchored/"),
        step("sea_longbeach", "2021-10-21", 0.0, "not_a_delay_effect",
             "Record 108 container ships waiting to dock (joint LA/LB complex figure).",
             "MESocal via Hellenic Shipping News: https://www.hellenicshippingnews.com/a-record-111-container-ships-are-floating-off-californias-busiest-ports-despite-bidens-24-7-schedule-and-looming-fines/"),
        step("gnd_inlandempire", "2021-10-25", 0.0, "not_a_delay_effect",
             "National truck-driver shortage quantified at 80,000 drivers (American Trucking Associations, Oct 2021) - the inland evacuation bottleneck behind terminal dwell; the source row names the dwell channel at gnd_inlandempire and gnd_chicago jointly.",
             "ATA via Western Overseas congestion update: https://www.westernoverseas.com/port-congestion-update-11-12-21/"),
        step("gnd_chicago", "2021-10-25", 0.0, "not_a_delay_effect",
             "Same ATA driver-shortage observation (80,000 drivers, Oct 2021) on the rail knock-on node named in the source row.",
             "ATA via Western Overseas congestion update: https://www.westernoverseas.com/port-congestion-update-11-12-21/"),
        step("sea_longbeach", "2021-10-29", 0.0, "not_a_delay_effect",
             "Harbor commissions vote the container dwell fee: $100/container/day, escalating $100/day, for boxes dwelling 9+ days (truck) / 6+ days (rail); data collection from 1 Nov, charging from 15 Nov (joint LA/LB measure).",
             "AP via Manufacturing.net: https://www.manufacturing.net/supply-chain/news/21808634/la-long-beach-ports-will-issue-fines-for-backlogged-cargo"),
        step("sea_losangeles", "2021-11-09", 0.0, "not_a_delay_effect",
             "New all-time record: 111 container ships waiting off the complex (joint LA/LB figure).",
             "MESocal via Hellenic Shipping News: https://www.hellenicshippingnews.com/a-record-111-container-ships-are-floating-off-californias-busiest-ports-despite-bidens-24-7-schedule-and-looming-fines/"),
        step("sea_losangeles", "2021-11-10", 14.9, "modeled_midpoint_of_observed_range",
             "164 ships in the complex; 109 container ships (79 at anchorage/drift, 30 at berth); average container-ship wait 13.7-16.1 days; ~700,000 containers waiting on the water. observed_delay_d 14.9 is the midpoint of the observed wait range.",
             "MESocal 10 Nov report via Western Overseas: https://www.westernoverseas.com/port-congestion-update-11-12-21/"),
        step("sea_longbeach", "2021-11-10", 14.9, "modeled_midpoint_of_observed_range",
             "Same joint MESocal 10 Nov report for the Long Beach side of the complex: average container-ship wait 13.7-16.1 days (midpoint 14.9 used).",
             "MESocal 10 Nov report via Western Overseas: https://www.westernoverseas.com/port-congestion-update-11-12-21/"),
        step("sea_losangeles", "2021-11-16", 0.0, "not_a_delay_effect",
             "New offshore queuing system takes effect: arriving container ships queue ~150 miles offshore in sailing-time order instead of anchoring in San Pedro Bay (queue count methodology changes from this date).",
             "PMA/PMSA/MESocal announcement via Riviera Maritime: https://www.rivieramm.com/news-content-hub/news-content-hub/ports-of-los-angeles-and-long-beach-introduce-new-queuing-process-to-ease-bottleneck-68472"),
    ],
    "unresolved_timeline_nodes": "air_losangeles is named as the mode-shift scenario node in the curated timeline but has no dated observed effect in the source table; no step was fabricated for it. All other named timeline nodes resolve to graph nodes.",
}

def main():
    graph = json.loads(GRAPH.read_text(encoding="utf-8"))
    ids = {n["id"] for n in graph["nodes"]}
    for data, fname in ((RED_SEA, "red_sea_2024.json"), (PANAMA, "panama_2023_24.json"), (LA, "la_2021.json")):
        assert data["disruption"]["node_id"] in ids, data["disruption"]["node_id"]
        loss = data["disruption"].get("capacity_loss")
        assert loss is not None and 0 < loss <= 1, (fname, loss)
        basis = data["disruption"].get("capacity_loss_basis")
        assert basis and basis.get("kind") in ("sourced", "derived", "modeled"), (fname, basis)
        assert basis.get("source") and basis.get("derivation"), (fname, basis)
        assert len(data["observed_steps"]) >= 8
        for s in data["observed_steps"]:
            assert s["node_id"] in ids, s["node_id"]
            assert set(("node_id", "observed_delay_d", "delay_basis", "effect", "evidence_source", "evidence_date")) <= set(s)
            assert s["delay_basis"], (fname, s)
        out = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
        (HERE / fname).write_text(out, encoding="utf-8")
        print(f"{fname}: steps={len(data['observed_steps'])} disruption_node={data['disruption']['node_id']} capacity_loss={loss} basis={basis['kind']} all node_ids resolve")

if __name__ == "__main__":
    main()
