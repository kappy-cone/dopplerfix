#!/bin/bash
# scenario sweep: realistic synth (all realism on), both design variants
S=/private/tmp/claude-501/-Users-williambarr-qgis/c334e685-7499-4d45-9232-dd5dbcafcf8c/scratchpad/skeptic_system
cd /Users/williambarr/qgis
PY=.venv/bin/python
run() { # name seed dur times
  name=$1; seed=$2; dur=$3; times=$4
  sc=$S/scn/$name
  if [ ! -f $sc/truth.json ]; then
    $PY cli.py synth --config $S/cfg_robust.yaml --out $sc --seed $seed --duration-s $dur --event-times $times > $S/log_synth_$name.txt 2>&1
  fi
  for v in robust strict; do
    $PY cli.py locate --config $S/cfg_$v.yaml --scenario $sc --run-id ${name}_$v --no-plots > $S/log_loc_${name}_$v.txt 2>&1
    rm -f $S/runs/${name}_$v/score.tif
  done
  rm -f $sc/audio.flac $sc/diagnostic.png
}
for s in 0 1 2 3 4 5 6 7; do run C$s $s 60 15,45; done
for s in 0 1 2 3; do run A$s $s 600 150,420; done
for s in 0 1 2; do run G$s $s 300 50,150,250; done
echo DONE > $S/driver_done.txt
