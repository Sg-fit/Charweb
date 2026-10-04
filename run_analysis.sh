#!/bin/bash
set -a; . /etc/charweb.env; set +a
A=./venv/bin/python
T=research/axis_test.py
echo "########## 3-class perception"
$A $T percept_v4.csv --label harness --perms 200
echo "########## visual perception: dom vs som"
$A $T percept_v4.csv --label harness --classes percept_dom,percept_som --perms 200
echo "########## pixel action: som vs vision"
$A $T percept_v4.csv --label harness --classes percept_som,percept_vision --perms 200
echo "########## mechanism: dom vs vision"
$A $T percept_v4.csv --label harness --classes percept_dom,percept_vision --perms 200
echo "########## perception LOTO"
$A research/m3_loto.py percept_v4.csv
echo "########## harness 4-class"
$A $T harness_v2.csv --label harness --perms 200
echo "########## harness LOTO"
$A research/m3_loto.py harness_v2.csv
echo "########## DONE"
