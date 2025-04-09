# SPDX-License-Identifier: Apache-2.0
r"""Benchmark online serving throughput.

On the server side, run one of the following commands:
    vLLM OpenAI API server
    vllm serve <your_model> \
        --swap-space 16 \
        --disable-log-requests

On the client side, run:
    python benchmarks/benchmark_serving.py \
        --backend <backend> \
        --model <your_model> \
        --dataset-name sharegpt \
        --dataset-path <path to dataset> \
        --request-rate <request_rate> \ # By default <request_rate> is inf
        --num-prompts <num_prompts> # By default <num_prompts> is 1000
        --gpu-supervised <gpu-supervised> # By default <gpu-supervised> is None, means all

    when using tgi backend, add
        --endpoint /generate_stream
    to the end of the command above.
"""
import argparse
import asyncio
import gc
import json
import os
import random
import time
import warnings
from collections.abc import AsyncGenerator, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import numpy as np
from backend_request_func import (ASYNC_REQUEST_FUNCS, RequestFuncInput,
                                  RequestFuncOutput)
from tqdm.asyncio import tqdm
from transformers import PreTrainedTokenizerBase

try:
    from vllm.transformers_utils.tokenizer import get_tokenizer
except ImportError:
    from backend_request_func import get_tokenizer

try:
    from vllm.utils import FlexibleArgumentParser
except ImportError:
    from argparse import ArgumentParser as FlexibleArgumentParser

from benchmark_dataset import (BurstGPTDataset, ConversationDataset,
                               InstructCoderDataset, RandomDataset,
                               SampleRequest, ShareGPTDataset, SonnetDataset,
                               VisionArenaDataset)
from benchmark_utils import convert_to_pytorch_benchmark_format, write_to_json

MILLISECONDS_TO_SECONDS_CONVERSION = 1000


@dataclass
class BenchmarkMetrics:
    completed: int
    total_input: int
    total_output: int
    request_throughput: float
    request_goodput: float
    output_throughput: float
    total_token_throughput: float
    mean_ttft_ms: float
    median_ttft_ms: float
    std_ttft_ms: float
    percentiles_ttft_ms: list[tuple[float, float]]
    mean_tpot_ms: float
    median_tpot_ms: float
    std_tpot_ms: float
    percentiles_tpot_ms: list[tuple[float, float]]
    mean_itl_ms: float
    median_itl_ms: float
    std_itl_ms: float
    percentiles_itl_ms: list[tuple[float, float]]
    # E2EL stands for end-to-end latency per request.
    # It is the time taken on the client side from sending
    # a request to receiving a complete response.
    mean_e2el_ms: float
    median_e2el_ms: float
    std_e2el_ms: float
    percentiles_e2el_ms: list[tuple[float, float]]


async def get_request(
    input_requests: list[SampleRequest],
    request_rate: float,
    burstiness: float = 1.0,
) -> AsyncGenerator[SampleRequest, None]:
    """
    Asynchronously generates requests at a specified rate
    with OPTIONAL burstiness.

    Args:
        input_requests:
            A list of input requests, each represented as a SampleRequest.
        request_rate:
            The rate at which requests are generated (requests/s).
        burstiness (optional):
            The burstiness factor of the request generation.
            Only takes effect when request_rate is not inf.
            Default value is 1, which follows a Poisson process.
            Otherwise, the request intervals follow a gamma distribution.
            A lower burstiness value (0 < burstiness < 1) results
            in more bursty requests, while a higher burstiness value
            (burstiness > 1) results in a more uniform arrival of requests.
    """
    input_requests: Iterable[SampleRequest] = iter(input_requests)

    # Calculate scale parameter theta to maintain the desired request_rate.
    assert burstiness > 0, (
        f"A positive burstiness factor is expected, but given {burstiness}.")
    theta = 1.0 / (request_rate * burstiness)

    for request in input_requests:
        yield request

        if request_rate == float("inf"):
            # If the request rate is infinity, then we don't need to wait.
            continue

        # Sample the request interval from the gamma distribution.
        # If burstiness is 1, it follows exponential distribution.
        interval = np.random.gamma(shape=burstiness, scale=theta)
        # The next request will be sent after the interval.
        await asyncio.sleep(interval)


def calculate_metrics(
    input_requests: list[SampleRequest],
    outputs: list[RequestFuncOutput],
    dur_s: float,
    tokenizer: PreTrainedTokenizerBase,
    selected_percentile_metrics: list[str],
    selected_percentiles: list[float],
    goodput_config_dict: dict[str, float],
) -> tuple[BenchmarkMetrics, list[int]]:
    actual_output_lens: list[int] = []
    total_input = 0
    completed = 0
    good_completed = 0
    itls: list[float] = []
    tpots: list[float] = []
    all_tpots: list[float] = []
    ttfts: list[float] = []
    e2els: list[float] = []
    for i in range(len(outputs)):
        if outputs[i].success:
            output_len = outputs[i].output_tokens

            if output_len is None:
                # We use the tokenizer to count the number of output tokens
                # for some serving backends instead of looking at
                # len(outputs[i].itl) since multiple output tokens may be
                # bundled together
                # Note : this may inflate the output token count slightly
                output_len = len(
                    tokenizer(outputs[i].generated_text,
                              add_special_tokens=False).input_ids)
            actual_output_lens.append(output_len)
            total_input += input_requests[i].prompt_len
            tpot = 0
            if output_len > 1:
                latency_minus_ttft = outputs[i].latency - outputs[i].ttft
                tpot = latency_minus_ttft / (output_len - 1)
                tpots.append(tpot)
            # Note: if output_len <= 1, we regard tpot as 0 for goodput
            all_tpots.append(tpot)
            itls += outputs[i].itl
            ttfts.append(outputs[i].ttft)
            e2els.append(outputs[i].latency)
            completed += 1
        else:
            actual_output_lens.append(0)

    if goodput_config_dict:
        valid_metrics = []
        slo_values = []

        if "ttft" in goodput_config_dict:
            valid_metrics.append(ttfts)
            slo_values.append(goodput_config_dict["ttft"] /
                              MILLISECONDS_TO_SECONDS_CONVERSION)
        if "tpot" in goodput_config_dict:
            valid_metrics.append(all_tpots)
            slo_values.append(goodput_config_dict["tpot"] /
                              MILLISECONDS_TO_SECONDS_CONVERSION)
        if "e2el" in goodput_config_dict:
            valid_metrics.append(e2els)
            slo_values.append(goodput_config_dict["e2el"] /
                              MILLISECONDS_TO_SECONDS_CONVERSION)

        for req_metric in zip(*valid_metrics):
            is_good_req = all([s >= r for s, r in zip(slo_values, req_metric)])
            if is_good_req:
                good_completed += 1

    if completed == 0:
        warnings.warn(
            "All requests failed. This is likely due to a misconfiguration "
            "on the benchmark arguments.",
            stacklevel=2)
    metrics = BenchmarkMetrics(
        completed=completed,
        total_input=total_input,
        total_output=sum(actual_output_lens),
        request_throughput=completed / dur_s,
        request_goodput=good_completed / dur_s,
        output_throughput=sum(actual_output_lens) / dur_s,
        total_token_throughput=(total_input + sum(actual_output_lens)) / dur_s,
        mean_ttft_ms=np.mean(ttfts or 0) *
        1000,  # ttfts is empty if streaming is not supported by backend
        std_ttft_ms=np.std(ttfts or 0) * 1000,
        median_ttft_ms=np.median(ttfts or 0) * 1000,
        percentiles_ttft_ms=[(p, np.percentile(ttfts or 0, p) * 1000)
                             for p in selected_percentiles],
        mean_tpot_ms=np.mean(tpots or 0) * 1000,
        std_tpot_ms=np.std(tpots or 0) * 1000,
        median_tpot_ms=np.median(tpots or 0) * 1000,
        percentiles_tpot_ms=[(p, np.percentile(tpots or 0, p) * 1000)
                             for p in selected_percentiles],
        mean_itl_ms=np.mean(itls or 0) * 1000,
        std_itl_ms=np.std(itls or 0) * 1000,
        median_itl_ms=np.median(itls or 0) * 1000,
        percentiles_itl_ms=[(p, np.percentile(itls or 0, p) * 1000)
                            for p in selected_percentiles],
        mean_e2el_ms=np.mean(e2els or 0) * 1000,
        std_e2el_ms=np.std(e2els or 0) * 1000,
        median_e2el_ms=np.median(e2els or 0) * 1000,
        percentiles_e2el_ms=[(p, np.percentile(e2els or 0, p) * 1000)
                             for p in selected_percentiles],
    )

    return metrics, actual_output_lens


async def benchmark(
    backend: str,
    api_url: str,
    base_url: str,
    model_id: str,
    model_name: str,
    tokenizer: PreTrainedTokenizerBase,
    input_requests: list[SampleRequest],
    logprobs: Optional[int],
    request_rate: float,
    burstiness: float,
    disable_tqdm: bool,
    profile: bool,
    selected_percentile_metrics: list[str],
    selected_percentiles: list[float],
    ignore_eos: bool,
    goodput_config_dict: dict[str, float],
    max_concurrency: Optional[int],
    lora_modules: Optional[Iterable[str]],
    gpu_ids: Optional[list[int]] = None,  # None: disable, []: all, [0,1]: specific GPUs
):
    """Run benchmark with optional GPU monitoring."""
    
    # Initialize backend request function
    if backend not in ASYNC_REQUEST_FUNCS:
        raise ValueError(f"Unknown backend: {backend}")
    request_func = ASYNC_REQUEST_FUNCS[backend]

    # Initial test request
    print("Starting initial single prompt test run...")
    test_request = input_requests[0]
    test_input = RequestFuncInput(
        model=model_id,
        model_name=model_name,
        prompt=test_request.prompt,
        api_url=api_url,
        prompt_len=test_request.prompt_len,
        output_len=test_request.expected_output_len,
        logprobs=logprobs,
        multi_modal_content=test_request.multi_modal_data,
        ignore_eos=ignore_eos,
    )

    test_output = await request_func(request_func_input=test_input)
    if not test_output.success:
        raise ValueError(f"Initial test failed: {test_output.error}")
    print("Initial test run completed. Starting main benchmark run...")

    # Setup LoRA modules if specified
    if lora_modules:
        lora_modules = iter([random.choice(lora_modules) for _ in range(len(input_requests))])

    # Start profiler if requested
    if profile:
        print("Starting profiler...")
        profile_input = RequestFuncInput(
            model=model_id,
            model_name=model_name,
            prompt=test_request.prompt,
            api_url=f"{base_url}/start_profile",
            prompt_len=test_request.prompt_len,
            output_len=test_request.expected_output_len,
            logprobs=logprobs,
            multi_modal_content=test_request.multi_modal_data,
            ignore_eos=ignore_eos
        )
        if (await request_func(profile_input)).success:
            print("Profiler started")

    # Print benchmark parameters
    print(f"Traffic request rate: {request_rate}")
    print(f"Burstiness factor: {burstiness} ({'Poisson' if burstiness == 1.0 else 'Gamma'})")
    print(f"Max concurrency: {max_concurrency}")

    # Initialize GPU monitoring
    gpu_stats = None
    monitor_task = None
    
    if gpu_ids is not None:  # Only initialize if GPU monitoring is requested
        try:
            import pynvml
            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()
            
            # Determine which GPUs to monitor
            monitored_gpus = gpu_ids if gpu_ids else list(range(device_count))
            monitored_gpus = sorted(set(
                gpu_id for gpu_id in monitored_gpus 
                if 0 <= gpu_id < device_count
            ))
            
            if monitored_gpus:
                print(f"Monitoring GPUs: {', '.join(map(str, monitored_gpus))}")
                
                # Initialize stats storage
                gpu_stats = {
                    'gpu_util': {i: [] for i in monitored_gpus},
                    'mem_util': {i: [] for i in monitored_gpus},
                    'mem_used': {i: [] for i in monitored_gpus},
                    'mem_total': {i: None for i in monitored_gpus},
                }
                
                # Get total memory for each GPU
                for i in monitored_gpus:
                    handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                    mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                    gpu_stats['mem_total'][i] = mem_info.total / (1024 ** 2)  # Convert to MB
                
                # Monitoring task
                async def monitor_gpu():
                    while True:
                        for i in monitored_gpus:
                            handle = pynvml.nvmlDeviceGetHandleByIndex(i)
                            try:
                                # Get GPU utilization
                                util = pynvml.nvmlDeviceGetUtilizationRates(handle)
                                gpu_stats['gpu_util'][i].append(util.gpu)
                                
                                # Get memory usage
                                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                                gpu_stats['mem_util'][i].append(
                                    mem_info.used / mem_info.total * 100
                                )
                                gpu_stats['mem_used'][i].append(
                                    mem_info.used / (1024 ** 2)
                                )
                            except pynvml.NVMLError as e:
                                print(f"Error monitoring GPU {i}: {e}")
                        await asyncio.sleep(1)  # Sample every second
                
                monitor_task = asyncio.create_task(monitor_gpu())
            else:
                print("No valid GPUs to monitor")
                
        except ImportError:
            print("pynvml not available, GPU monitoring disabled")
        except Exception as e:
            print(f"GPU monitoring initialization failed: {e}")
            if 'pynvml' in locals():
                try:
                    pynvml.nvmlShutdown()
                except:
                    pass
    
    # Setup progress bar
    pbar = None if disable_tqdm else tqdm(total=len(input_requests))
    semaphore = asyncio.Semaphore(max_concurrency) if max_concurrency else None

    async def limited_request_func(request_input, pbar):
        if semaphore is None:
            return await request_func(request_input, pbar)
        async with semaphore:
            return await request_func(request_input, pbar)
        
    # Run benchmark
    benchmark_start_time = time.perf_counter()
    tasks = []
    
    async for request in get_request(input_requests, request_rate, burstiness):
        req_model_id, req_model_name = model_id, model_name
        if lora_modules:
            req_lora_module = next(lora_modules)
            req_model_id = req_model_name = req_lora_module

        request_input = RequestFuncInput(
            model=req_model_id,
            model_name=req_model_name,
            prompt=request.prompt,
            api_url=api_url,
            prompt_len=request.prompt_len,
            output_len=request.expected_output_len,
            logprobs=logprobs,
            multi_modal_content=request.multi_modal_data,
            ignore_eos=ignore_eos
        )
        
        tasks.append(asyncio.create_task(
            limited_request_func(request_input, pbar)
        ))
    
    outputs = await asyncio.gather(*tasks)

    # Stop GPU monitoring
    if monitor_task:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    
    if 'pynvml' in locals():
        try:
            pynvml.nvmlShutdown()
        except:
            pass

    # Stop profiler if running
    if profile:
        print("Stopping profiler...")
        profile_input = RequestFuncInput(
            model=model_id,
            prompt=test_request.prompt,
            api_url=f"{base_url}/stop_profile",
            prompt_len=test_request.prompt_len,
            output_len=test_request.expected_output_len,
            logprobs=logprobs,
        )
        if (await request_func(profile_input)).success:
            print("Profiler stopped")

    if pbar is not None:
        pbar.close()

    # Calculate metrics
    benchmark_duration = time.perf_counter() - benchmark_start_time
    metrics, actual_output_lens = calculate_metrics(
        input_requests=input_requests,
        outputs=outputs,
        dur_s=benchmark_duration,
        tokenizer=tokenizer,
        selected_percentile_metrics=selected_percentile_metrics,
        selected_percentiles=selected_percentiles,
        goodput_config_dict=goodput_config_dict,
    )

    # Prepare results
    result = {
        "duration": benchmark_duration,
        "completed": metrics.completed,
        "total_input_tokens": metrics.total_input,
        "total_output_tokens": metrics.total_output,
        "request_throughput": metrics.request_throughput,
        "request_goodput": metrics.request_goodput if goodput_config_dict else None,
        "output_throughput": metrics.output_throughput,
        "total_token_throughput": metrics.total_token_throughput,
        "input_lens": [o.prompt_len for o in outputs],
        "output_lens": actual_output_lens,
        "ttfts": [o.ttft for o in outputs],
        "itls": [o.itl for o in outputs],
        "generated_texts": [o.generated_text for o in outputs],
        "errors": [o.error for o in outputs],
        "gpu_stats": None,  # Will be filled below if GPU monitoring was active
    }

    # Calculate and add GPU statistics if monitoring was active
    if gpu_stats and gpu_stats['gpu_util']:
        gpu_results = {}
        for gpu_id in gpu_stats['gpu_util'].keys():
            if not gpu_stats['gpu_util'][gpu_id]:  # Skip if no data
                continue
                
            utils = gpu_stats['gpu_util'][gpu_id]
            mem_utils = gpu_stats['mem_util'][gpu_id]
            mem_used = gpu_stats['mem_used'][gpu_id]
            mem_total = gpu_stats['mem_total'][gpu_id]
            
            gpu_results[f"gpu_{gpu_id}"] = {
                'avg_gpu_util': sum(utils) / len(utils),
                'max_gpu_util': max(utils),
                'avg_mem_util': sum(mem_utils) / len(mem_utils),
                'max_mem_util': max(mem_utils),
                'avg_mem_used': sum(mem_used) / len(mem_used),
                'max_mem_used': max(mem_used),
                'mem_total': mem_total,
            }
        
        result["gpu_stats"] = gpu_results

    # Print summary
    print("\n" + "="*55)
    print("{:^55}".format(" Benchmark Results "))
    print("="*55)
    print("{:<30} {:>18}".format("Successful requests:", metrics.completed))
    print("{:<30} {:>18.2f} s".format("Duration:", benchmark_duration))
    print("{:<30} {:>18}".format("Input tokens:", metrics.total_input))
    print("{:<30} {:>18}".format("Output tokens:", metrics.total_output))
    print("{:<30} {:>18.2f} req/s".format("Request throughput:", metrics.request_throughput))
    print("{:<30} {:>18.2f} tok/s".format("Token throughput:", metrics.total_token_throughput))

    # Print GPU stats if available
    if result.get("gpu_stats"):
        print("\n" + "-"*55)
        print("{:^55}".format(" GPU Statistics "))
        print("-"*55)
        for gpu_id, stats in result["gpu_stats"].items():
            print(f"\n[GPU {gpu_id.split('_')[1]}]")
            print("{:<43} {:>8.2f}%".format("Avg GPU util:", stats['avg_gpu_util']))
            print("{:<43} {:>8.2f}%".format("Max GPU util:", stats['max_gpu_util']))
            print("{:<43} {:>8.2f}%".format("Avg Mem util:", stats['avg_mem_util']))
            print("{:<43} {:>8.2f}%".format("Max Mem util:", stats['max_mem_util']))
            print("{:<43} {:>8.2f} MB".format("Avg Mem used:", stats['avg_mem_used']))
            print("{:<43} {:>8.2f} MB".format("Max Mem used:", stats['max_mem_used']))
            print("{:<43} {:>8.2f} MB".format("Total Mem:", stats['mem_total']))

    # Print latency metrics
    def print_metric(metric, name):
        if metric in selected_percentile_metrics:
            print("\n" + "-"*55)
            print("{:^55}".format(f" {name} Metrics "))
            print("-"*55)
            print("{:<43} {:>8.2f} ms".format(
                "Mean:", getattr(metrics, f"mean_{metric}_ms")))
            print("{:<43} {:>8.2f} ms".format(
                "Median:", getattr(metrics, f"median_{metric}_ms")))
            for p, val in getattr(metrics, f"percentiles_{metric}_ms"):
                print("{:<43} {:>8.2f} ms".format(
                    f"P{int(p) if p.is_integer() else p}:", val))

    print_metric("ttft", "Time To First Token")
    print_metric("tpot", "Time Per Output Token")
    print_metric("itl", "Inter-Token Latency")
    print_metric("e2el", "End-to-End Latency")

    print("\n" + "="*55)
    return result


def check_goodput_args(args):
    # Check and parse goodput arguments
    goodput_config_dict = {}
    VALID_NAMES = ["ttft", "tpot", "e2el"]
    if args.goodput:
        goodput_config_dict = parse_goodput(args.goodput)
        for slo_name, slo_val in goodput_config_dict.items():
            if slo_name not in VALID_NAMES:
                raise ValueError(
                    f"Invalid metric name found, {slo_name}: {slo_val}. "
                    "The service level objective name should be one of "
                    f"{str(VALID_NAMES)}. ")
            if slo_val < 0:
                raise ValueError(
                    f"Invalid value found, {slo_name}: {slo_val}. "
                    "The service level objective value should be "
                    "non-negative.")
    return goodput_config_dict


def parse_goodput(slo_pairs):
    goodput_config_dict = {}
    try:
        for slo_pair in slo_pairs:
            slo_name, slo_val = slo_pair.split(":")
            goodput_config_dict[slo_name] = float(slo_val)
    except ValueError as err:
        raise argparse.ArgumentTypeError(
            "Invalid format found for service level objectives. "
            "Specify service level objectives for goodput as \"KEY:VALUE\" "
            "pairs, where the key is a metric name, and the value is a "
            "number in milliseconds.") from err
    return goodput_config_dict


def save_to_pytorch_benchmark_format(args: argparse.Namespace,
                                     results: dict[str, Any],
                                     file_name: str) -> None:
    metrics = [
        "median_ttft_ms", "mean_ttft_ms", "std_ttft_ms", "p99_ttft_ms",
        "mean_tpot_ms", "median_tpot_ms", "std_tpot_ms", "p99_tpot_ms",
        "median_itl_ms", "mean_itl_ms", "std_itl_ms", "p99_itl_ms"
    ]
    # These raw data might be useful, but they are rather big. They can be added
    # later if needed
    ignored_metrics = ["ttfts", "itls", "generated_texts", "errors"]
    pt_records = convert_to_pytorch_benchmark_format(
        args=args,
        metrics={k: [results[k]]
                 for k in metrics},
        extra_info={
            k: results[k]
            for k in results if k not in metrics and k not in ignored_metrics
        })
    if pt_records:
        # Don't use json suffix here as we don't want CI to pick it up
        pt_file = f"{os.path.splitext(file_name)[0]}.pytorch.json"
        write_to_json(pt_file, pt_records)


def main(args: argparse.Namespace):
    
    # 解析gpu_supervised参数
    if args.gpu_supervised == "-1":
        gpu_ids = []  # 监控所有GPU
    elif args.gpu_supervised == "-2":  # -2表示不监控
        gpu_ids = None
    else:
        try:
            # 处理单个数字或多个用逗号分隔的数字
            gpu_ids = []
            parts = args.gpu_supervised.split(",")
            for part in parts:
                if part.strip():  # 确保不是空字符串
                    gpu_ids.append(int(part.strip()))
        except ValueError:
            raise ValueError(
                "Invalid GPU IDs format. Use like '0' or '0,1,2' or '-1' for all GPUs. "
                "Use empty string '' to disable GPU monitoring."
            )
    
    print(args)
    random.seed(args.seed)
    np.random.seed(args.seed)

    backend = args.backend
    model_id = args.model
    model_name = args.served_model_name
    tokenizer_id = args.tokenizer if args.tokenizer is not None else args.model
    tokenizer_mode = args.tokenizer_mode

    if args.base_url is not None:
        api_url = f"{args.base_url}{args.endpoint}"
        base_url = f"{args.base_url}"
    else:
        api_url = f"http://{args.host}:{args.port}{args.endpoint}"
        base_url = f"http://{args.host}:{args.port}"

    tokenizer = get_tokenizer(tokenizer_id,
                              tokenizer_mode=tokenizer_mode,
                              trust_remote_code=args.trust_remote_code)

    if args.dataset_name is None:
        raise ValueError(
            "Please specify '--dataset-name' and the corresponding "
            "'--dataset-path' if required.")

    if args.dataset_name == "sonnet":
        dataset = SonnetDataset(dataset_path=args.dataset_path)
        # For the "sonnet" dataset, formatting depends on the backend.
        if args.backend == "openai-chat":
            input_requests = dataset.sample(num_requests=args.num_prompts,
                                            input_len=args.sonnet_input_len,
                                            output_len=args.sonnet_output_len,
                                            prefix_len=args.sonnet_prefix_len,
                                            tokenizer=tokenizer,
                                            return_prompt_formatted=False)
        else:
            assert tokenizer.chat_template or tokenizer.default_chat_template, (
                "Tokenizer/model must have chat template for sonnet dataset.")
            input_requests = dataset.sample(num_requests=args.num_prompts,
                                            input_len=args.sonnet_input_len,
                                            output_len=args.sonnet_output_len,
                                            prefix_len=args.sonnet_prefix_len,
                                            tokenizer=tokenizer,
                                            return_prompt_formatted=True)

    elif args.dataset_name == "hf":
        # all following datasets are implemented from the
        # HuggingFaceDataset base class
        if args.dataset_path in VisionArenaDataset.SUPPORTED_DATASET_PATHS:
            dataset_class = VisionArenaDataset
            args.hf_split = "train"
            args.hf_subset = None
        elif args.dataset_path in InstructCoderDataset.SUPPORTED_DATASET_PATHS:
            dataset_class = InstructCoderDataset
            args.hf_split = "train"
        elif args.dataset_path in ConversationDataset.SUPPORTED_DATASET_PATHS:
            dataset_class = ConversationDataset
        input_requests = dataset_class(
            dataset_path=args.dataset_path,
            dataset_subset=args.hf_subset,
            dataset_split=args.hf_split,
        ).sample(
            num_requests=args.num_prompts,
            tokenizer=tokenizer,
            random_seed=args.seed,
            output_len=args.hf_output_len,
        )

    else:
        # For datasets that follow a similar structure, use a mapping.
        dataset_mapping = {
            "sharegpt":
            lambda: ShareGPTDataset(random_seed=args.seed,
                                    dataset_path=args.dataset_path).sample(
                                        tokenizer=tokenizer,
                                        num_requests=args.num_prompts,
                                        output_len=args.sharegpt_output_len,
                                    ),
            "burstgpt":
            lambda: BurstGPTDataset(random_seed=args.seed,
                                    dataset_path=args.dataset_path).
            sample(tokenizer=tokenizer, num_requests=args.num_prompts),
            "random":
            lambda: RandomDataset(dataset_path=args.dataset_path).sample(
                tokenizer=tokenizer,
                num_requests=args.num_prompts,
                prefix_len=args.random_prefix_len,
                input_len=args.random_input_len,
                output_len=args.random_output_len,
                range_ratio=args.random_range_ratio,
            )
        }

        try:
            input_requests = dataset_mapping[args.dataset_name]()
        except KeyError as err:
            raise ValueError(f"Unknown dataset: {args.dataset_name}") from err
    goodput_config_dict = check_goodput_args(args)

    # Avoid GC processing "static" data - reduce pause times.
    gc.collect()
    gc.freeze()

    benchmark_result = asyncio.run(
        benchmark(
            backend=backend,
            api_url=api_url,
            base_url=base_url,
            model_id=model_id,
            model_name=model_name,
            tokenizer=tokenizer,
            input_requests=input_requests,
            logprobs=args.logprobs,
            request_rate=args.request_rate,
            burstiness=args.burstiness,
            disable_tqdm=args.disable_tqdm,
            profile=args.profile,
            selected_percentile_metrics=args.percentile_metrics.split(","),
            selected_percentiles=[
                float(p) for p in args.metric_percentiles.split(",")
            ],
            ignore_eos=args.ignore_eos,
            goodput_config_dict=goodput_config_dict,
            max_concurrency=args.max_concurrency,
            lora_modules=args.lora_modules,
            gpu_ids=gpu_ids
        ))

    # Save config and results to json
    if args.save_result:
        result_json: dict[str, Any] = {}

        # Setup
        current_dt = datetime.now().strftime("%Y%m%d-%H%M%S")
        result_json["date"] = current_dt
        result_json["backend"] = backend
        result_json["model_id"] = model_id
        result_json["tokenizer_id"] = tokenizer_id
        result_json["num_prompts"] = args.num_prompts

        # Metadata
        if args.metadata:
            for item in args.metadata:
                if "=" in item:
                    kvstring = item.split("=")
                    result_json[kvstring[0].strip()] = kvstring[1].strip()
                else:
                    raise ValueError(
                        "Invalid metadata format. Please use KEY=VALUE format."
                    )

        if not args.save_detailed:
            # Remove fields with too many data points
            for field in [
                    "input_lens", "output_lens", "ttfts", "itls",
                    "generated_texts", "errors"
            ]:
                if field in result_json:
                    del result_json[field]

        # Traffic
        result_json["request_rate"] = (args.request_rate if args.request_rate
                                       < float("inf") else "inf")
        result_json["burstiness"] = args.burstiness
        result_json["max_concurrency"] = args.max_concurrency

        # Merge with benchmark result
        result_json = {**result_json, **benchmark_result}

        # Save to file
        base_model_id = model_id.split("/")[-1]
        max_concurrency_str = (f"-concurrency{args.max_concurrency}"
                               if args.max_concurrency is not None else "")
        file_name = f"{backend}-{args.request_rate}qps{max_concurrency_str}-{base_model_id}-{current_dt}.json"  #noqa
        if args.result_filename:
            file_name = args.result_filename
        if args.result_dir:
            file_name = os.path.join(args.result_dir, file_name)
        with open(file_name, "w", encoding='utf-8') as outfile:
            json.dump(result_json, outfile)
        save_to_pytorch_benchmark_format(args, result_json, file_name)


if __name__ == "__main__":
    parser = FlexibleArgumentParser(
        description="Benchmark the online serving throughput.")
    parser.add_argument(
        "--backend",
        type=str,
        default="vllm",
        choices=list(ASYNC_REQUEST_FUNCS.keys()),
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Server or API base url if not using http host and port.",
    )
    # Use 127.0.0.1 here instead of localhost to force the use of ipv4
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--endpoint",
        type=str,
        default="/v1/completions",
        help="API endpoint.",
    )
    parser.add_argument(
        "--dataset-name",
        type=str,
        default="sharegpt",
        choices=["sharegpt", "burstgpt", "sonnet", "random", "hf"],
        help="Name of the dataset to benchmark on.",
    )
    parser.add_argument("--dataset-path",
                        type=str,
                        default=None,
                        help="Path to the sharegpt/sonnet dataset. "
                        "Or the huggingface dataset ID if using HF dataset.")
    parser.add_argument(
        "--max-concurrency",
        type=int,
        default=None,
        help="Maximum number of concurrent requests. This can be used "
        "to help simulate an environment where a higher level component "
        "is enforcing a maximum number of concurrent requests. While the "
        "--request-rate argument controls the rate at which requests are "
        "initiated, this argument will control how many are actually allowed "
        "to execute at a time. This means that when used in combination, the "
        "actual request rate may be lower than specified with --request-rate, "
        "if the server is not processing requests fast enough to keep up.")

    parser.add_argument(
        "--model",
        type=str,
        required=True,
        help="Name of the model.",
    )
    parser.add_argument(
        "--tokenizer",
        type=str,
        help=
        "Name or path of the tokenizer, if not using the default tokenizer.",  # noqa: E501
    )
    parser.add_argument("--use-beam-search", action="store_true")
    parser.add_argument(
        "--num-prompts",
        type=int,
        default=1000,
        help="Number of prompts to process.",
    )
    parser.add_argument(
        "--gpu-supervised",
        type=str,
        default="-1",
        help="Decide which gpu will be supervised.",
    )
    parser.add_argument(
        "--logprobs",
        type=int,
        default=None,
        help=("Number of logprobs-per-token to compute & return as part of "
              "the request. If unspecified, then either (1) if beam search "
              "is disabled, no logprobs are computed & a single dummy "
              "logprob is returned for each token; or (2) if beam search "
              "is enabled 1 logprob per token is computed"),
    )
    parser.add_argument(
        "--request-rate",
        type=float,
        default=float("inf"),
        help="Number of requests per second. If this is inf, "
        "then all the requests are sent at time 0. "
        "Otherwise, we use Poisson process or gamma distribution "
        "to synthesize the request arrival times.",
    )
    parser.add_argument(
        "--burstiness",
        type=float,
        default=1.0,
        help="Burstiness factor of the request generation. "
        "Only take effect when request_rate is not inf. "
        "Default value is 1, which follows Poisson process. "
        "Otherwise, the request intervals follow a gamma distribution. "
        "A lower burstiness value (0 < burstiness < 1) results in more "
        "bursty requests. A higher burstiness value (burstiness > 1) "
        "results in a more uniform arrival of requests.",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Trust remote code from huggingface",
    )
    parser.add_argument(
        "--disable-tqdm",
        action="store_true",
        help="Specify to disable tqdm progress bar.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Use Torch Profiler. The endpoint must be launched with "
        "VLLM_TORCH_PROFILER_DIR to enable profiler.",
    )
    parser.add_argument(
        "--save-result",
        action="store_true",
        help="Specify to save benchmark results to a json file",
    )
    parser.add_argument(
        "--save-detailed",
        action="store_true",
        help="When saving the results, whether to include per request "
        "information such as response, error, ttfs, tpots, etc.",
    )
    parser.add_argument(
        "--metadata",
        metavar="KEY=VALUE",
        nargs="*",
        help="Key-value pairs (e.g, --metadata version=0.3.3 tp=1) "
        "for metadata of this run to be saved in the result JSON file "
        "for record keeping purposes.",
    )
    parser.add_argument(
        "--result-dir",
        type=str,
        default=None,
        help="Specify directory to save benchmark json results."
        "If not specified, results are saved in the current directory.",
    )
    parser.add_argument(
        "--result-filename",
        type=str,
        default=None,
        help="Specify the filename to save benchmark json results."
        "If not specified, results will be saved in "
        "{backend}-{args.request_rate}qps-{base_model_id}-{current_dt}.json"
        " format.",
    )
    parser.add_argument(
        "--ignore-eos",
        action="store_true",
        help="Set ignore_eos flag when sending the benchmark request."
        "Warning: ignore_eos is not supported in deepspeed_mii and tgi.")
    parser.add_argument(
        "--percentile-metrics",
        type=str,
        default="ttft,tpot,itl",
        help="Comma-seperated list of selected metrics to report percentils. "
        "This argument specifies the metrics to report percentiles. "
        "Allowed metric names are \"ttft\", \"tpot\", \"itl\", \"e2el\". "
        "Default value is \"ttft,tpot,itl\".")
    parser.add_argument(
        "--metric-percentiles",
        type=str,
        default="99",
        help="Comma-seperated list of percentiles for selected metrics. "
        "To report 25-th, 50-th, and 75-th percentiles, use \"25,50,75\". "
        "Default value is \"99\". "
        "Use \"--percentile-metrics\" to select metrics.",
    )
    parser.add_argument(
        "--goodput",
        nargs="+",
        required=False,
        help="Specify service level objectives for goodput as \"KEY:VALUE\" "
        "pairs, where the key is a metric name, and the value is in "
        "milliseconds. Multiple \"KEY:VALUE\" pairs can be provided, "
        "separated by spaces. Allowed request level metric names are "
        "\"ttft\", \"tpot\", \"e2el\". For more context on the definition of "
        "goodput, refer to DistServe paper: https://arxiv.org/pdf/2401.09670 "
        "and the blog: https://hao-ai-lab.github.io/blogs/distserve")

    # group for dataset specific arguments
    sonnet_group = parser.add_argument_group("sonnet dataset options")
    sonnet_group.add_argument(
        "--sonnet-input-len",
        type=int,
        default=550,
        help=
        "Number of input tokens per request, used only for sonnet dataset.",
    )
    sonnet_group.add_argument(
        "--sonnet-output-len",
        type=int,
        default=150,
        help=
        "Number of output tokens per request, used only for sonnet dataset.",
    )
    sonnet_group.add_argument(
        "--sonnet-prefix-len",
        type=int,
        default=200,
        help=
        "Number of prefix tokens per request, used only for sonnet dataset.",
    )

    sharegpt_group = parser.add_argument_group("sharegpt dataset options")
    sharegpt_group.add_argument(
        "--sharegpt-output-len",
        type=int,
        default=None,
        help="Output length for each request. Overrides the output length "
        "from the ShareGPT dataset.")

    random_group = parser.add_argument_group("random dataset options")
    random_group.add_argument(
        "--random-input-len",
        type=int,
        default=1024,
        help=
        "Number of input tokens per request, used only for random sampling.",
    )
    random_group.add_argument(
        "--random-output-len",
        type=int,
        default=128,
        help=
        "Number of output tokens per request, used only for random sampling.",
    )
    random_group.add_argument(
        "--random-range-ratio",
        type=float,
        default=1.0,
        help="Range of sampled ratio of input/output length, "
        "used only for random sampling.",
    )
    random_group.add_argument(
        "--random-prefix-len",
        type=int,
        default=0,
        help="Number of fixed prefix tokens before random "
        " context. The length range of context in a random "
        " request is [random-prefix-len, "
        " random-prefix-len + random-prefix-len * random-range-ratio).")

    hf_group = parser.add_argument_group("hf dataset options")
    hf_group.add_argument("--hf-subset",
                          type=str,
                          default=None,
                          help="Subset of the HF dataset.")
    hf_group.add_argument("--hf-split",
                          type=str,
                          default=None,
                          help="Split of the HF dataset.")
    hf_group.add_argument(
        "--hf-output-len",
        type=int,
        default=None,
        help="Output length for each request. Overrides the output lengths "
        "from the sampled HF dataset.",
    )

    parser.add_argument(
        '--tokenizer-mode',
        type=str,
        default="auto",
        choices=['auto', 'slow', 'mistral', 'custom'],
        help='The tokenizer mode.\n\n* "auto" will use the '
        'fast tokenizer if available.\n* "slow" will '
        'always use the slow tokenizer. \n* '
        '"mistral" will always use the `mistral_common` tokenizer. \n*'
        '"custom" will use --tokenizer to select the preregistered tokenizer.')

    parser.add_argument("--served-model-name",
                        type=str,
                        default=None,
                        help="The model name used in the API. "
                        "If not specified, the model name will be the "
                        "same as the ``--model`` argument. ")

    parser.add_argument("--lora-modules",
                        nargs='+',
                        default=None,
                        help="A subset of LoRA module names passed in when "
                        "launching the server. For each request, the "
                        "script chooses a LoRA module at random.")

    args = parser.parse_args()

    main(args)
