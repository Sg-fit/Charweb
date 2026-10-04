#!/bin/bash
A=./venv/bin/python
T=research/bootstrap_ci.py
echo "########## harness (4 classes)"
$A $T harness_M2.csv --label harness --boots 2000
echo "########## model (2 models)"
$A $T modelaxis_v5.csv --label model --boots 2000
echo "########## perception 3-class"
$A $T percept_M2.csv --label harness --boots 2000
echo "########## perception mechanism"
$A $T percept_M2.csv --label harness --classes percept_dom,percept_vision --boots 2000
echo "########## DONE"
