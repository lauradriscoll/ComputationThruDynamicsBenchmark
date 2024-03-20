# hash:sha256:923a8bced7c55e74ef39ce6e7f6ad9ed95da7e1d36355be99f1d40b0e7c404d0
ARG REGISTRY_HOST
FROM $REGISTRY_HOST/codeocean/cuda-miniconda-jupyterlab:latest

ARG DEBIAN_FRONTEND=noninteractive

ARG GIT_ASKPASS
ARG GIT_ACCESS_TOKEN
COPY git-askpass /

RUN pip install -U --no-cache-dir \
    gymnasium \
    h5py \
    hydra-core \
    imageio[ffmpeg] \
    ipykernel \
    ipywidgets \
    matplotlib \
    motornet \
    omegaconf \
    opencv-python \
    python-dotenv \
    pytorch-lightning==2.1.3 \
    ray[tune] \
    scikit-learn \
    torch==1.13.1 \
    torchaudio==0.13.1 \
    torchdata==0.5.1 \
    torchmetrics==1.3.0.post0 \
    torchvision==0.14.1 \
    wandb
