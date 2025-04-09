# LLM-benchmark
This is an improved code for the LLM benchmark, based on the vllm benchmark script.  
It supports various LLM inference frameworks, including vLLM, and supports monitoring the GPU performance metrics of specified GPU IDs.  
All monitoring configurations can be adjusted through the command line.  

## Start a service  
Use docker to start vllm container, meanwhile load a LLM file.  
  
```bash
./start_vllm_container.sh
```
  You can adjust some configurations.  
```bash
  -n, --name        容器名称 (默认: vllm-kxdu)
  -i, --image       Docker镜像 (默认: vllm/vllm-openai:v0.7.3)
  -g, --gpus        GPU设备ID (默认: 1)
  -p, --port        主机端口 (默认: 10018)
  -m, --model       模型路径 (默认: /storagedata/common/models/Qwen/Qwen2_5-0_5B-Instruct)
  -u, --util        GPU内存利用率 (默认: 0.2)
  -h, --host-path   宿主机路径 (默认: /storagedata)
  -c, --container-path 容器内路径 (默认: /storagedata)
  -w, --wait        最大等待时间(秒) (默认: 300)
  -h, --help        显示帮助信息
```
For example
```bash
./start_vllm_container.sh -g 0,1,3 -p 114514 -u 0.5 -m /models/Qwen/Qwen2.5-7B-Instruct
```
  
## benchmark

Then run the benchmarking script

```bash
# download dataset
# wget https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json
python3 LLM-benchmark/benchmark_serving.py \
    --backend <backend> \
    --model <model_path> \
    --endpoint <endpoint> \
    --dataset-name <dataset_name> \
    --dataset-path <dataset_path> \
    --num-prompts <num_prompts> \
    --max-concurrency <concurrency> \
    --gpu-supervised <gpu-supervised> \
    --port <port>
```
For example
```bash
python3 ./LLM-benchmark/benchmark_serving.py   --backend vllm --model /Qwen/Qwen2_5-0_5B-Instruct --endpoint /v1/completions --dataset-name sharegpt --dataset-path /storagedata/common/data/ShareGPT_V3_unfiltered_cleaned_split.json   --num-prompts 16 --max-concurrency 4 --gpu-supervised 2 --port 10018
```

If successful, you will see the following output

```
Starting initial single prompt test run...
Initial test run completed. Starting main benchmark run...
Traffic request rate: inf
Burstiness factor: 1.0 (Poisson)
Max concurrency: 4
Monitoring GPUs: 2
100%|██████████████████████████| 16/16 [00:03<00:00,  5.14it/s]

=======================================================
                   Benchmark Results                   
=======================================================
Successful requests:                           16
Duration:                                    3.12 s
Input tokens:                                3211
Output tokens:                               3673
Request throughput:                          5.14 req/s
Token throughput:                         2209.48 tok/s

-------------------------------------------------------
                    GPU Statistics                     
-------------------------------------------------------

[GPU 2]
Avg GPU util:                                  68.75%
Max GPU util:                                  73.00%
Avg Mem util:                                  45.99%
Max Mem util:                                  46.04%
Avg Mem used:                               11296.62 MB
Max Mem used:                               11309.12 MB
Total Mem:                                  24564.00 MB

-------------------------------------------------------
              Time To First Token Metrics              
-------------------------------------------------------
Mean:                                          49.32 ms
Median:                                        31.12 ms
P99:                                          104.26 ms

-------------------------------------------------------
             Time Per Output Token Metrics             
-------------------------------------------------------
Mean:                                           2.96 ms
Median:                                         2.95 ms
P99:                                            3.27 ms

-------------------------------------------------------
              Inter-Token Latency Metrics              
-------------------------------------------------------
Mean:                                           2.98 ms
Median:                                         2.82 ms
P99:                                            8.30 ms

=======================================================

```
