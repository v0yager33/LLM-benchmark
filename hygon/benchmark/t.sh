
NUM_PROMPTS=4

CONCURRENCIES=(2 4 8 16)

for concurrency in "${CONCURRENCIES[@]}"; do
    REQ_NUM=$(( NUM_PROMPTS * concurrency ))
    echo "Total REQ : $REQ_NUM"
done
