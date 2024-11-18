#!/usr/bin/env bash

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

cd ${SCRIPT_DIR}
cd modules/ibow-lcd
rm -Rf build 

cd ${SCRIPT_DIR}
cd modules/obindex2/lib
rm -Rf build 

cd ${SCRIPT_DIR}
rm -Rf build 
rm -Rf lib