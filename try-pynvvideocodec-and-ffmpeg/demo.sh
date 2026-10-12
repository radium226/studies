#!/usr/bin/env bash
# Run every combination of backend (gpu, cpu) and transformer (plain PyTorch, ONNX, SafeTensors)
set -euo pipefail

cd "$(dirname "${0}")"

output_dir="demo"
input="${output_dir}/input.mp4"
onnx_model="${output_dir}/circle.onnx"
safetensors_weights="${output_dir}/circle.safetensors"

mkdir -p "${output_dir}"

if [[ ! -f "${input}" ]]; then
  echo "==> Generating ${input}"
  ./generate-sample.sh "${input}" >/dev/null
fi

echo "==> Exporting ${onnx_model} and ${safetensors_weights}"
uv run draw-circle export-onnx --color 255 0 0 "${onnx_model}"
uv run draw-circle export-safetensors --color 255 0 0 "${safetensors_weights}"

for backend in gpu cpu; do
  for transformer in torch onnx safetensors; do
    output="${output_dir}/output-${backend}-${transformer}.mp4"
    echo
    echo "==> --backend=${backend}, transformer: ${transformer} → ${output}"
    case "${transformer}" in
      torch) uv run draw-circle run --backend="${backend}" "${input}" "${output}" ;;
      onnx) uv run draw-circle run --backend="${backend}" --onnx-model="${onnx_model}" "${input}" "${output}" ;;
      safetensors) uv run draw-circle run --backend="${backend}" --safetensors-weights="${safetensors_weights}" "${input}" "${output}" ;;
    esac
  done
done
