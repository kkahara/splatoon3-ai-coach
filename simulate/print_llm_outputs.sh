for f in "analysis/2026-09-10 15-58-36/coach_prototype/"*reasoning.output.json; do
    echo
    echo "========== $f =========="
    cat "$f"
done