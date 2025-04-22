
MODEL_PATH="/root/public_data/model/admin/DeepSeek-R1-Distill-Llama-70B/"

SERVERD_MODEL_NAME=R1-70B
vllm serve ${MODEL_PATH} --tensor-parallel-size 4 \
	--max-model-len 8192 \
	--served-model-name ${SERVERD_MODEL_NAME} \
	--enforce-eager \
	--gpu-memory-utilization 0.95 \
	--swap-space 8 \
	--enable-prefix-caching \
	--tokenizer-pool-size 8 \
	--max-num-seqs 8 \
	--enable-chunked-prefill  \
	--max-num-batched-tokens 40960  \
	--host 0.0.0.0 --disable-log-stats
