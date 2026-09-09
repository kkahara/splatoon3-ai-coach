for f in "analysis/2026-09-06 22-35-09/coach_prototype/"*.output.json; do
    echo
    echo "========== $f =========="
    cat "$f"
done