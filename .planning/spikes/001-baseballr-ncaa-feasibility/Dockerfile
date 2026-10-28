FROM rocker/tidyverse:4.5.2

ARG BASEBALLR_REF=v2.0.0
ENV BASEBALLR_REF=${BASEBALLR_REF}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        fonts-liberation \
    && curl -fsSL -o /tmp/google-chrome.deb \
        https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
    && apt-get install -y --no-install-recommends /tmp/google-chrome.deb \
    && rm -f /tmp/google-chrome.deb \
    && rm -rf /var/lib/apt/lists/*

RUN R -q -e 'install.packages(c("chromote", "remotes"), repos = "https://cloud.r-project.org")' \
    && R -q -e 'remotes::install_github(paste0("BillPetti/baseballr@", Sys.getenv("BASEBALLR_REF", "v2.0.0")), dependencies = NA, upgrade = "never")'

ENV CHROMOTE_CHROME=/usr/bin/google-chrome \
    NCAA_CHROMOTE_TESTS=1

WORKDIR /spike

COPY run_spike.R /spike/run_spike.R

ENTRYPOINT ["Rscript", "/spike/run_spike.R"]
