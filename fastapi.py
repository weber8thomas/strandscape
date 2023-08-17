import datetime
import logging
import os
import subprocess
from fastapi import Body, FastAPI
import pika
import json
import uvicorn

app = FastAPI()

FILENAME = "latest_status.json"


def load_from_json():
    """Load the data from the JSON file."""
    try:
        with open("latest_status.json", "r") as file:
            data = json.load(file)
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        # If the file does not exist or there's an error in reading it,
        # return an empty dictionary or other default value
        return {}


def consume_last_message_from_rabbitmq():
    connection = pika.BlockingConnection(pika.ConnectionParameters("localhost"))
    channel = connection.channel()

    # Fetch the message without auto acknowledgment
    method_frame, header_frame, body = channel.basic_get(
        queue="my_queue", auto_ack=False
    )
    print(method_frame, header_frame, body)

    if method_frame:
        # Extract the timestamp from the header frame
        if header_frame.timestamp:
            timestamp = header_frame.timestamp
            human_readable_timestamp = datetime.datetime.fromtimestamp(
                timestamp / 1000.0
            ).strftime("%Y-%m-%d %H:%M:%S")
            print(human_readable_timestamp)

        else:
            timestamp = None
        # Convert timestamp to human-readable format if necessary

        # # Acknowledge the message after processing
        # channel.basic_ack(delivery_tag=method_frame.delivery_tag)
        print("TOTOTO")
        connection.close()
        data = json.loads(body.decode("utf-8"))
        if data["workflows"] == [] and os.path.exists(FILENAME):
            print("RabbitMQ queue NOT empty but message is")
            print("Loading from JSON file...")
            data_json = load_from_json()
            file_timestamp = os.path.getmtime(FILENAME)
            file_timestamp = datetime.datetime.fromtimestamp(file_timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            print(data_json)
            return data_json, file_timestamp
        else:
            print("RabbitMQ queue NOT empty and message is NOT empty")
            return data, human_readable_timestamp

    else:
        if os.path.exists(FILENAME):
            connection.close()
            print("No message available, RabbitMQ queue is empty")
            print("Loading from JSON file...")
            data_json = load_from_json()
            file_timestamp = os.path.getmtime(FILENAME)
            file_timestamp = datetime.datetime.fromtimestamp(file_timestamp).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            print(data_json)
            return data_json, file_timestamp
        else:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {"workflows": []}, current_time


@app.get("/get-progress")
def get_progress():
    data, timestamp = consume_last_message_from_rabbitmq()
    print(data, timestamp)
    return data, timestamp


@app.post("/trigger-snakemake/{run_id}")
def trigger_snakemake(run_id: str, snake_args: dict = Body(...)):

    print(run_id)

    data_location = "/scratch/tweber/DATA/MC_DATA/STOCKS"
    publishdir_location = "/g/korbel/WORKFLOW_RESULTS"
    profile_slurm = ["--profile", "workflow/snakemake_profiles/HPC/slurm_EMBL/"]
    profile_dry_run = ["--profile", "workflow/snakemake_profiles/local/conda/"]
    dry_run_options = ["-c", "1", "-n", "-q"]
    snakemake_binary = "/g/korbel2/weber/miniconda3/envs/snakemake_latest/bin/snakemake"
    wms_monitor_options = "http://localhost:8058"
    wms_monitor_renaming_option = f"name={run_id}"
    sample_name = f"{run_id}".split("--")[1]

    # Append the snake_args to cmd
    snake_args_list = list()
    for key, value in snake_args.items():
        if value is not None:
            snake_args_list.append(f"{key}={value}")

    cmd = [
        f"{snakemake_binary}",
        "--nolock",
        "--rerun-triggers mtime",
        "--config",
        "genecore=True",
        "split_qc_plot=False",
        # f"publishdir={publishdir_location}",
        # "email=thomas.weber@embl.de",
        f"data_location={data_location}",
        f'samples_to_process="[{sample_name}]"',
    ]

    wms_monitor_args = [
        f"--wms-monitor {wms_monitor_options}",
        f"--wms-monitor-args {wms_monitor_renaming_option}",
    ]

    print(cmd + snake_args_list + wms_monitor_args)

    def execute_command(self, directory_path, prefix):
        """Execute the command."""

        # Change directory and run the snakemake command
        date_folder = directory_path.split("/")[-1]

        logging.info(
            "Running command: %s", " ".join(cmd + profile_dry_run + dry_run_options)
        )

        process = subprocess.Popen(
            cmd + profile_dry_run + dry_run_options,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )

        # Variable to store the penultimate line
        penultimate_line = ""

        # Read the output line by line in real-time
        for line in iter(process.stdout.readline, ""):
            logging.info(line.strip())  # log line in real-time
            if line.strip():  # If line is not blank
                penultimate_line = line.strip()

        # Wait for the subprocess to finish
        process.wait()
        logging.info("Return code: %s", process.returncode)

        # Check the penultimate line
        if str(process.returncode) == str(0):
            self.run_second_command(cmd, profile_slurm, data_location, date_folder)
        else:
            logging.info("\nThe output is not as expected.")

    def run_second_command(self, cmd, profile_slurm, data_location, date_folder):
        """Run the second command and write the output to a log file."""

        logging.info("\nThe output is as expected.")
        logging.info("Running command: %s", " ".join(cmd + profile_slurm))

        os.makedirs("watchdog/logs/per-run", exist_ok=True)

        # Get the current date and time
        now = datetime.now()

        # Convert it to a string
        current_time = now.strftime("%Y%m%d%H%M%S")

        with open(f"watchdog/logs/per-run/{date_folder}_{current_time}.log", "w") as f:
            process2 = subprocess.Popen(
                cmd + profile_slurm, stdout=f, stderr=f, universal_newlines=True
            )
            process2.wait()

            logging.info("Return code: %s", process2.returncode)

        # Change the permissions of the new directory
        subprocess.run(["chmod", "-R", "777", f"{data_location}/{date_folder}"])


if __name__ == "__main__":
    uvicorn.run(app, host="localhost", port=8059)