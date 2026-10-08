set -e
export SUMO_HOME=/usr/share/sumo
T=/usr/share/sumo/tools/randomTrips.py
cd /tmp && cp /work/erlangen.net.xml /work/erlangen.poly.xml .
for N in 50 300 600; do
  P=$(python3 -c "print(round(100/$N, 4))")
  python3 $T -n erlangen.net.xml -b 0 -e 100 -p $P --seed 42 --min-distance 1000 \
     --vehicle-class passenger --validate --prefix v \
     --trip-attributes 'departLane="best" departSpeed="max"' \
     -o trips_$N.xml -r routes_$N.rou.xml > gen_$N.txt 2>&1 || { tail -5 gen_$N.txt; exit 1; }
  sumo -n erlangen.net.xml -r routes_$N.rou.xml --begin 0 --end 200 --no-step-log \
       --summary-output sum_$N.xml --duration-log.statistics > log_$N.txt 2>&1
  python3 - "$N" <<'PY'
import sys, xml.etree.ElementTree as ET
N = sys.argv[1]
steps = ET.parse(f"sum_{N}.xml").getroot().findall("step")
run = [int(s.get("running")) for s in steps]
ins = int(steps[-1].get("inserted")); wait = int(steps[-1].get("waiting"))
gen = len(ET.parse(f"routes_{N}.rou.xml").getroot().findall("vehicle"))
print(f"N={N}: routes={gen} inserted@200s={ins} waiting={wait} running mean={sum(run)/len(run):.0f} max={max(run)} @200s={run[-1]}")
PY
  cp routes_$N.rou.xml /calib/routes_$N.rou.xml
done
