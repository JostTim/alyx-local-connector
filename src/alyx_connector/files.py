# built-ins imports
import shutil
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

# custom imports
from pint import Quantity
from rich import print
from rich.text import Text
from rich.console import Console, Group
from rich.progress import Progress, BarColumn, TextColumn
from rich.panel import Panel, Padding
from rich.live import Live
from rich.prompt import Prompt

from pandas import DataFrame, Series


class FileTransferManager:

    results: DataFrame
    direction: str
    sessions: DataFrame

    def __init__(self, sessions, results=None, direction=None):

        if isinstance(sessions, Series):
            sessions = sessions.to_frame().transpose()

        self.sessions = sessions
        self.results = results  # type: ignore
        self.direction = direction  # type: ignore

    @staticmethod
    def from_transfer(transfer):
        return FileTransferManager(transfer.sessions, transfer.results, transfer.direction)

    def _assert_file_checked(self, function_name):
        if self.results is None or self.direction is None:
            raise ValueError(
                f"Cannot show {function_name} if no result has been "
                "obtained through a fetch or a push_request command"
            )

    def resolve(self):

        results = self.results.copy()
        conflicts = results[results["decision"] == "conflict"]
        accepted_decisions = ["transfer", "overwrite", "ignore", "conflict"]

        def conflict_pannel(conflict=None):

            if conflict is None:
                return Panel("", title="❗ Handling Conflict :", border_style="dark_blue")

            line = Text.assemble(
                ("📄"),
                (f"{conflict.source_filepath}\n", "steel_blue1 reverse"),
                ("⏬\n"),
                (f"{conflict.destination_filepath}\n", "steel_blue1 reverse"),
                ("Informations : 🚩:", "dark_blue underline"),
                (f"{conflict.warnings}\n\n", "dark_blue"),
                (f"Type in your decision. (One of {accepted_decisions})"),
            )

            return Panel(line, title="❗ Handling Conflict :", border_style="dark_blue")

        if len(conflicts):
            console = Console()
            with Live(conflict_pannel(), console=console, refresh_per_second=5) as live:
                for index, conflited_file in results[results["decision"] == "conflict"].iterrows():
                    live.update(conflict_pannel(conflited_file))

                    decision = Prompt.ask(choices=accepted_decisions)
                    if decision not in accepted_decisions:
                        raise ValueError(f"Must be one of {accepted_decisions}, got {decision}")
                    else:
                        results.loc[index, "decision"] = decision

                conflicts = results[results["decision"] == "conflict"]
                if len(conflicts):
                    print(
                        Text(
                            "🚫 There seem to still exists conflicts to resolve. "
                            "Please run resolve again to fix the last ones 🚫",
                            style="bright_red bold",
                        )
                    )
                else:
                    print(Text("All conflicts solved ! 🎉", style="spring_green3 bold"))

        else:
            print(Text("No conflicts to resolve. ✅", style="spring_green3 bold"))

        return FileTransferManager(self.sessions, results=results, direction=self.direction)

    def status(self, show_message=True, return_messages=False):
        self._assert_file_checked("status")

        action = self.direction

        is_status_ok = True
        messages = []

        n_ignored_files = len(self.results[self.results["decision"] == "ignore"])
        n_transfered_files = len(self.results[self.results["decision"] == "transfer"])
        n_overritten_files = len(self.results[self.results["decision"] == "overwrite"])
        n_conflicts = len(self.results[self.results["decision"] == "conflict"])

        messages.append(Text("❗ Conflicting files:", style="dark_blue underline bold"))
        if n_conflicts:
            is_status_ok = False
            messages.append(
                Text(
                    f"\t🚫 {n_conflicts} conflicting files were found.\n"
                    "\tPlease sort out the conflits with the `.resolve()` command.\n",
                    style="bold bright_red",
                )
            )
        else:
            messages.append(Text("\tNo conflicts were found ! 🎉\n", style="bold spring_green3"))

        metrics = self.transfer_metrics()
        messages.append(Text("📈 Transfer metrics:", style="dark_blue underline bold"))

        messages.append(Text(f"\t• {n_transfered_files} files will be newly transfered.", style="spring_green3"))
        messages.append(
            Text(
                f"\t• {n_overritten_files} files will overwrite the equivalent file in destination.",
                style="dark_orange",
            )
        )
        messages.append(
            Text(
                f"\t• {n_ignored_files} files will be ignored " "(not transfered from source to destination).\n",
                style="dark_orange",
            )
        )

        transfer_destinations = metrics.groupby("destination")

        messages.append(
            Text(
                f"💽 {action.capitalize()}ing to {len(transfer_destinations)} locations:",
                style="dark_blue underline bold",
            )
        )

        for destination, metric in transfer_destinations:

            destination = str(destination)
            sources = ", ".join(metric.source.astype(str))  # type: ignore
            transfer_space = metric.transfer_space.sum()
            free_space = metric.free_space.iloc[0]

            enough_space = free_space > transfer_space
            color = "spring_green3" if enough_space else "bright_red"
            summary = "✅ Enough free space ✅" if enough_space else "❌ Not enough space ! ❌"

            transfer_space_text = f"{transfer_space.to_compact():.2f~P} "
            free_space_text = f"{free_space.to_compact():.2f~P}"

            min_length = 11

            sources = sources.rjust(min_length)
            fill_size_source = "." * (len(sources) - (len(transfer_space_text)))
            fill_description_source = "." * (len(sources) - len("Source "))

            line = Text.assemble(
                (f"• {summary}\n", color),
                ("  "),
                ("Source", f"{color} underline"),
                (" "),
                (fill_description_source, "grey62"),
                (" 🆚 ", color),
                ("Destination\n", f"{color} underline"),
                (f"  {transfer_space_text}", f"{color} bold"),
                (fill_size_source, "grey62"),
                (" ⏩ ", color),
                (f"{free_space_text}\n  ", f"{color} bold"),
                (sources, f"{color} reverse"),
                (" ⏩ ", color),
                (f"{destination}\n", f"{color} reverse"),
            )

            messages.append(Padding(Panel(line, border_style=color), (0, 0, 0, 6)))

            if not enough_space:
                is_status_ok = False

        messages.append(Text("🌠 Conclusion:", style="dark_blue underline bold"))

        if is_status_ok:
            messages.append(Text(f"\t• ✅ Able to {action} the data.", style="spring_green3"))
            messages.append(
                Text(
                    f"\t• 📊 In total {metrics.transfer_space.sum().to_compact():.2f~P} of data will be " f"{action}ed",
                    style="spring_green3",
                )
            )
        else:
            messages.append(
                Text(
                    f"\t• ❌ Cannot {action} the data. Please sort out the issues mentionned above", style="bright_red"
                )
            )

        if show_message:
            group = Group(*messages)
            panel = Panel(
                group,
                title=f"📟 Pre-{action.capitalize()}ing Status Report",
                width=100,
                border_style="light_steel_blue3",
            )
            print(panel)

        if return_messages:
            return messages

        return is_status_ok

    def transfer_metrics(self):
        self._assert_file_checked("status")

        transfers_infos = []
        for (source, destination), transfers in self.results.groupby(["source_volume", "destination_volume"]):
            free_space = shutil.disk_usage(destination).free * Quantity("bytes")

            transfered_files = transfers[transfers["decision"] == "transfer"]
            overwritten_files = transfers[transfers["decision"] == "overwrite"]

            transfer_space = (
                overwritten_files["destination_filesize"] - overwritten_files["source_filesize"]
            ).sum() + transfered_files["source_filesize"].sum()

            session_nb = len(transfers.session.unique())
            files_nb = len(transfers)

            transfers_infos.append(
                dict(
                    source=source,
                    destination=destination,
                    free_space=free_space,
                    transfer_space=transfer_space,
                    session_nb=session_nb,
                    files_nb=files_nb,
                )
            )

        return DataFrame(transfers_infos)

    def _source_repositories(self):
        return self._repositories("source")

    def _destination_repositories(self):
        return self._repositories("destination")

    def _repositories(self, localisation="source"):
        self._assert_file_checked("sources_directories")
        if localisation not in ["source", "destination"]:
            raise ValueError("_repositories localisation must be 'source' or 'destination'")

        def replace_element(root_path, rel_path):
            return str(root_path).replace(str(rel_path), " ")

        if self.direction == "push":
            if localisation == "source":
                repo_key = "local_path"
            else:
                repo_key = "remote_path"
        else:
            if localisation == "source":
                repo_key = "local_path"
            else:
                repo_key = "local_path"

        root_paths = self.sessions.apply(lambda row: replace_element(row[repo_key], row["rel_path"]), axis=1)
        return root_paths.unique().tolist()

    def push(self):
        self._assert_file_checked("push")
        if self.direction != "push":
            raise ValueError("Cannot push files without doing first a prepush check")
        return self.transfer()

    def pull(self):
        self._assert_file_checked("pull")
        if self.direction != "pull":
            raise ValueError("Cannot pull files without doing first a fetch check")
        return self.transfer()

    def transfer(self):
        status = self.status(show_message=False, return_messages=False)
        if not status:
            raise ValueError(f"Cannot {self.direction} the data. Please check issues with `.status()`")

        transfers = self.results[(self.results["decision"] == "transfer") | (self.results["decision"] == "overwrite")]
        transfer_results = self.copy_files_with_progress(
            transfers.source_filepath, transfers.destination_filepath, transfers.decision
        )
        # TODO : write a message here if transfer errors, using the transfer_results.success column
        print(Text(f"🎉 Finished {self.direction}ing successfully ! 🎉", style="spring_green3"))
        return transfer_results

    @staticmethod
    def copy_files_with_progress(src_paths, dst_paths, decisions, max_workers=4):

        def copy_file(src, dst, decision, progress, task_id):

            src, dst = Path(src), Path(dst)

            message = f"Failed to copy {src} to {dst}."
            success = False

            if not src.exists():
                return src, dst, success, f"{message} Source doesn't exist"

            if dst.exists() and decision != "overwrite":
                return (
                    src,
                    dst,
                    success,
                    (
                        f"{message} "
                        "Decision was set to {decision} but the destination existed already. "
                        "Decision should have been set to overwrite"
                    ),
                )

            dst.parent.mkdir(exist_ok=True, parents=True)

            try:
                shutil.copy2(src, dst)
                message = f"Copied {src} to {dst}"
                success = True
            except Exception as e:
                message = f"{message}: Unexpected error : {e}"

            progress.advance(task_id)
            return src, dst, success, message

        total_files = len(src_paths)

        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("({task.completed}/{task.total})"),
        ) as progress:
            task_id = progress.add_task("Copying files...", total=total_files)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                copy_results = []
                futures = [
                    executor.submit(copy_file, src, dst, dec, progress, task_id)
                    for src, dst, dec in zip(src_paths, dst_paths, decisions)
                ]
                for future in as_completed(futures):
                    src, dst, success, message = future.result()
                    copy_results.append(
                        dict(source_filepath=src, destination_filepath=dst, success=success, message=message)
                    )
        return DataFrame(copy_results)

    def fetch(self, policies=None, show_status=True):
        results = self.check_files(source="remote_path", destination="local_path", policies=policies)
        new_file_namager = FileTransferManager(self.sessions, results, direction="pull")
        if show_status:
            new_file_namager.status()
        return new_file_namager

    def push_request(self, policies=None, show_status=True):
        results = self.check_files(source="local_path", destination="remote_path", policies=policies)
        new_file_namager = FileTransferManager(self.sessions, results, direction="push")
        if show_status:
            new_file_namager.status()
        return new_file_namager

    def check_files(self, source="remote_path", destination="local_path", policies=None):

        console = Console()

        default_policies = {
            "close_dates": "conflict",
            "destination_older": "overwrite",
            "no_file_exists": "transfer",
            "destination_younger": "ignore",
            "close_dates_threshold": 10,
        }

        if policies is not None:
            default_policies.update(policies)

        policies = default_policies

        def get_volume(full_path, rel_path):
            full_path, rel_path = str(Path(full_path)), str(Path(rel_path))
            root_path = Path(full_path.replace(rel_path, ""))
            return Path(root_path.drive)

        results = []

        with Progress(console=console) as progress:

            task = progress.add_task(f"Checking files for {len(self.sessions)} sessions", total=len(self.sessions))

            for _, session in self.sessions.iterrows():

                source_path = Path(str(session[source]))
                destination_path = Path(str(session[destination]))

                source_volume = get_volume(source_path, session["rel_path"])
                destination_volume = get_volume(destination_path, session["rel_path"])

                for root, dirs, files in source_path.walk():

                    for file in files:

                        source_filepath = root / file
                        source_filesize = source_filepath.stat().st_size * Quantity("bytes")
                        relative_filepath = source_filepath.relative_to(source_path)
                        destination_filepath = destination_path / relative_filepath

                        destination_exists = destination_filepath.exists()

                        source_stat = source_filepath.stat()

                        source_creation_date = source_stat.st_birthtime
                        source_modification_date = source_stat.st_mtime

                        source_date = max(source_creation_date, source_modification_date)

                        if destination_exists:

                            destination_stat = destination_filepath.stat()

                            destination_creation_date = destination_stat.st_birthtime
                            destination_modification_date = destination_stat.st_mtime

                            destination_filesize = destination_stat.st_size * Quantity("bytes")

                            destination_date = max(destination_creation_date, destination_modification_date)

                            if (difference := abs(source_date - destination_date)) < policies["close_dates_threshold"]:
                                decision = policies["close_dates"]
                                warnings = (
                                    f"The destination file and the source file last modification dates "
                                    f"are very close ({difference} sec difference). "
                                    "Double checking the file contents before transfer might be safer."
                                )

                            elif destination_date > source_date:
                                decision = policies["destination_younger"]
                                warnings = (
                                    "The destination file date is more recent than the source file one. "
                                    "Double checking the file contents before transfering is mandatory !"
                                )

                            else:
                                decision = policies["destination_older"]
                                warnings = "The destination file is older than source. "
                                "Transfering will most likely be okay."

                        else:
                            decision = policies["no_file_exists"]
                            warnings = "File is not existing on the destination, transfering is absolutely okay."
                            destination_date = None
                            destination_filesize = 0 * Quantity("bytes")

                        record = dict(
                            source_filepath=source_filepath,
                            destination_filepath=destination_filepath,
                            source_filesize=source_filesize,
                            destination_filesize=destination_filesize,
                            relative_filepath=relative_filepath,
                            destination_exists=destination_exists,
                            source_date=source_date,
                            destination_date=destination_date,
                            source_volume=source_volume,
                            destination_volume=destination_volume,
                            decision=decision,
                            warnings=warnings,
                            session=session.u_alias,
                        )

                        results.append(record)
                progress.advance(task)

        return DataFrame(results)
