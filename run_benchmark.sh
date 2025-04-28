#!/bin/bash

# 测试配置参数
log_dir="/benchmark/logs/20250428"
script_path="/benchmark/benchmark_serving.py"
model_path="/data/DeepSeek-R1-Distill-Qwen-7B"
served_model_name="llama_65b"
endpoint="/v1/chat/completions"
dataset="random"
test_rounds=8
port=1025
trust_remote_code="--trust-remote-code"

concurrencies=(8 16)
input_lengths=(4096)
output_lengths=(1024)

# log记录参数
model_name="DeepSeek-R1-Distill-Qwen-7B"
model_name_short="deepseekqwen7b"
accelerator_type="Ascend-310P3"
accelerator_number=4
backend="Mindie"

# 脚本配置参数
sleep_time=1

# 创建日志目录
mkdir -p "$log_dir"

# 循环遍历并发数、输入长度、输出长度
for concurrency in "${concurrencies[@]}"; do
    for input_len in "${input_lengths[@]}"; do
        for output_len in "${output_lengths[@]}"; do
            # 生成日志文件名，包含精确时间戳
            log_file="${log_dir}/benchmark_${backend}_${model_name_short}_${accelerator_number}Unit_${accelerator_type}_InLen${input_len}_OutLen${output_len}_Con${concurrency}_$(date +'%Y%m%d_%H%M%S%3N').log"

            #计算总请求数
	    req_num=$(( test_rounds * concurrency ))
            echo "Total REQ : $req_num"

            # 打印测试开始信息到日志文件
            echo "==============================================" | tee "$log_file"
            echo "Starting test: Backend=${backend}, Model=${model_name}" | tee -a "$log_file"
	    echo "Input length=${input_len}, Output length=${output_len}, Concurrency=${concurrency}" | tee -a "$log_file"
	    echo "Rounds=${test_rounds}, Total Requests : ${req_num}" | tee -a "$log_file"
            echo "Accelerator: ${accelerator_type}, Accelerator Number: ${accelerator_number}" | tee -a "$log_file"
            echo "Log file: ${log_file}" | tee -a "$log_file"
            echo "==============================================" | tee -a "$log_file"

            # 执行测试命令并将输出记录到日志文件
            nohup python3  "$script_path" \
                --served-model-name "$served_model_name" \
                --backend openai-chat \
                --model "$model_path" \
                --endpoint "$endpoint" \
                --dataset-name "$dataset" \
                --num-prompts "$req_num" \
                --max-concurrency "$concurrency" \
                --random-input-len "$input_len" \
                --random-output-len "$output_len" \
                --port "$port" \
                $trust_remote_code \
                2>&1 | tee -a "$log_file"

            # 检查命令执行状态
            if [ ${PIPESTATUS[0]} -ne 0 ]; then
                echo "[ERROR] Test failed!" | tee -a "$log_file"
                # 可以选择继续执行后续测试，而不是直接退出
                # exit 1
            else
                echo "Test completed." | tee -a "$log_file"
            fi

            # 每次测试完成后进行 sleep 操作
            sleep $sleep_time
        done
    done
done
