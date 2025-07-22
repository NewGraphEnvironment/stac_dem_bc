my_tab_caption_rmd <- function(
    caption_text = my_caption,
    tip_flag = TRUE,
    tip_text = " <b>NOTE: To view all columns in the table - please click on one of the sort arrows within column headers before scrolling to the right.</b>") {
  
  cat(
    '<div style="text-align: center; font-weight: bold; margin-bottom: 10px;">',
    caption_text,
    if (tip_flag) tip_text,
    '</div>',
    sep = "\n"
  )
}

vm_upload_run <- function(droplet, file_name, path_remote,
                          run_only = FALSE,
                          upload_only = FALSE) {
  # build remote paths
  remote_dest <- if (fs::is_absolute_path(path_remote)) path_remote else fs::path(".", path_remote)
  remote_script <- fs::path(path_remote,
                            fs::path_file(file_name))  # e.g. "config/stac_update.sh"
  
  if (run_only) {
    # just execute the already-uploaded script
    droplet |>
      analogsea::droplet_ssh(remote_script)
    
  } else if (upload_only) {
    # only upload & chmod
    droplet |>
      analogsea::droplet_upload(file_name, remote_dest) |>
      analogsea::droplet_ssh(
        paste("chmod 0700", remote_script)
      )
    
  } else {
    # default: upload, chmod, then run
    droplet |>
      analogsea::droplet_upload(file_name, remote_dest) |>
      analogsea::droplet_ssh(
        paste("chmod 0700", remote_script)
      ) |>
      analogsea::droplet_ssh(remote_script)
  }
}


# Example:
# vm_upload_run(d, "scripts/config/stac_update.sh", "config")

