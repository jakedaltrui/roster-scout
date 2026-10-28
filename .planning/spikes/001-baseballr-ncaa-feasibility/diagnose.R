suppressPackageStartupMessages({
  library(baseballr)
  library(jsonlite)
  library(rvest)
})

results_dir <- "/spike/results"
lookup <- as.data.frame(baseballr::load_ncaa_baseball_teams())
seasons <- as.data.frame(baseballr::load_ncaa_baseball_season_ids())
year <- 2026L

lookup$year <- suppressWarnings(as.integer(lookup$year))
lookup$division <- suppressWarnings(as.integer(lookup$division))
season <- seasons[seasons$season == year, , drop = FALSE]

targets <- lookup[
  !is.na(lookup$year) & !is.na(lookup$season_team_id) & !is.na(lookup$team_id) &
  lookup$year == year & (
    lookup$season_team_id == 615349 |
    lookup$team_id %in% c(234, 689, 622)
  ),
  , drop = FALSE
]

fetch_metadata <- function(label, url) {
  messages <- character()
  started <- Sys.time()
  response <- tryCatch(
    withCallingHandlers(
      baseballr:::request_with_proxy(url),
      message = function(m) {
        messages <<- c(messages, conditionMessage(m))
        invokeRestart("muffleMessage")
      }
    ),
    error = function(e) e
  )
  duration <- as.numeric(difftime(Sys.time(), started, units = "secs"))

  if (inherits(response, "error")) {
    return(list(label = label, url = url, duration_seconds = round(duration, 3),
                error = conditionMessage(response), messages = unique(messages)))
  }

  body <- httr2::resp_body_string(response)
  document <- tryCatch(xml2::read_html(body), error = function(e) NULL)
  hrefs <- if (is.null(document)) character() else html_attr(html_elements(document, "a"), "href")
  table_headers <- if (is.null(document)) list() else lapply(html_elements(document, "table"), function(table) {
    trimws(html_text2(html_elements(table, "th")))
  })

  list(
    label = label,
    url = url,
    duration_seconds = round(duration, 3),
    status = httr2::resp_status(response),
    body_bytes = nchar(body, type = "bytes"),
    blocked = baseballr:::.ncaa_is_blocked(body),
    interstitial = baseballr:::.ncaa_is_interstitial(body),
    table_count = if (is.null(document)) 0L else length(html_elements(document, "table")),
    has_stat_grid = if (is.null(document)) FALSE else length(html_elements(document, "#stat_grid")) > 0,
    relevant_links = unique(hrefs[grepl("roster|season_to_date_stats|stats", hrefs)]),
    table_headers = table_headers,
    messages = unique(messages),
    error = NULL
  )
}

diagnostics <- list()
for (i in seq_len(nrow(targets))) {
  target <- targets[i, , drop = FALSE]
  team_id <- as.character(target$team_id[[1]])
  season_team_id <- as.character(target$season_team_id[[1]])
  division <- as.integer(target$division[[1]])
  prefix <- paste0("d", division, "_", gsub("[^a-z0-9]+", "_", tolower(target$team_name[[1]])))

  diagnostics[[paste0(prefix, "_legacy_roster")]] <- fetch_metadata(
    paste0(prefix, "_legacy_roster"),
    paste0("https://stats.ncaa.org/team/", team_id, "/roster/", season$id[[1]])
  )
  diagnostics[[paste0(prefix, "_modern_team")]] <- fetch_metadata(
    paste0(prefix, "_modern_team"),
    paste0("https://stats.ncaa.org/teams/", season_team_id)
  )

  for (stat_type in c("batting", "pitching", "fielding")) {
    category_id <- season[[paste0(stat_type, "_id")]][[1]]
    diagnostics[[paste0(prefix, "_", stat_type)]] <- fetch_metadata(
      paste0(prefix, "_", stat_type),
      paste0("https://stats.ncaa.org/teams/", season_team_id,
             "/season_to_date_stats?year_stat_category_id=", category_id)
    )
  }
}

write_json(
  list(
    year = year,
    season_ids = season,
    matched_targets = targets,
    exact_615349 = lookup[!is.na(lookup$season_team_id) & lookup$season_team_id == 615349, , drop = FALSE],
    diagnostics = diagnostics
  ),
  file.path(results_dir, "diagnostic.json"),
  dataframe = "rows", auto_unbox = TRUE, pretty = TRUE, na = "null"
)

try(baseballr:::.ncaa_browser_close(), silent = TRUE)
