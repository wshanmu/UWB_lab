# UWB Range-Only Lab Guide

This lab uses two Qorvo DWM3001CDK UWB boards to run FiRa two-way ranging
between two wrists or forearms. You will use the inter-board distance signal for
ranging analysis and bimanual activity recognition.

The folder distributed for this lab is named `UWB_LAB` and already contains the
`uwb-qorvo-tools` subfolder. The scripts in this folder use relative paths, so
the folder can be placed anywhere on your laptop.

Each board should already have the UCI firmware. Do not reflash the boards
unless the instructor explicitly asks you to.

## 1. Set Up The Qorvo Tool

Use Terminal on macOS. Use PowerShell or Anaconda PowerShell Prompt on Windows.
The Windows examples below use PowerShell syntax.

The full Qorvo install command, `python -m pip install .`, is not required for
this lab. We will run the Python scripts directly from the distributed folder.
The important part is to tell Python where the Qorvo libraries are.

macOS:

```bash
cd UWB_LAB/uwb-qorvo-tools
conda activate cosmos-ds
export UWB_TOOLS=$(pwd)
export PYTHONPATH="$UWB_TOOLS/lib/uwb-uci:$UWB_TOOLS/lib/uqt-utils:$UWB_TOOLS:$PYTHONPATH"
```

Windows PowerShell:

```powershell
cd UWB_LAB\uwb-qorvo-tools
conda activate cosmos-ds
$env:UWB_TOOLS=(Get-Location).Path
$env:PYTHONPATH="$env:UWB_TOOLS\lib\uwb-uci;$env:UWB_TOOLS\lib\uqt-utils;$env:UWB_TOOLS;$env:PYTHONPATH"
```

If Python later reports `No module named serial` or `No module named colorama`,
install the small runtime dependencies:

```bash
python -m pip install pyserial colorama toml
```

## 2. Identify The Serial Ports

Connect both DWM3001CDK boards over USB.

macOS:

```bash
ls /dev/cu.usbmodem*
```

Windows:

Open Device Manager and look under `Ports (COM & LPT)`. You should see two COM
ports, such as `COM7` and `COM8`.

You need one controller/initiator board and one controlee/responder board.
Record both port names.

macOS setup example:

```bash
export CONTROLLER_PORT=/dev/cu.usbmodemD46FFE3655DD1
export CONTROLEE_PORT=/dev/cu.usbmodemE89E195B6C731
export GROUP_ID=10
```

Windows PowerShell setup example:

```powershell
$env:CONTROLLER_PORT="COM7"
$env:CONTROLEE_PORT="COM8"
$env:GROUP_ID="10"
```

`export` on macOS and `$env:` on Windows create terminal variables that later
commands can read. They only work inside the current terminal. If you open a new
terminal or tab, run these setup commands again.

## 3. Check Both Boards

Run this once for each board.

macOS:

```bash
python scripts/device/get_device_info/get_device_info.py -p $CONTROLLER_PORT
python scripts/device/get_device_info/get_device_info.py -p $CONTROLEE_PORT
```

Windows PowerShell:

```powershell
python scripts\device\get_device_info\get_device_info.py -p $env:CONTROLLER_PORT
python scripts\device\get_device_info\get_device_info.py -p $env:CONTROLEE_PORT
```

Both commands should report `status: Ok`.

If a board does not respond, check that no other terminal is using the same
port, unplug and reconnect the board, then run the command again.

## 4. Use Your Group Preamble Index

Each group must use its assigned group ID as the FiRa preamble index:

```text
--preamble-idx <your group id>
```

For example, group 1 uses:

```text
--preamble-idx 1
```

Both boards in the same group must use the same value. This helps reduce
interference when multiple groups range at the same time.

## 5. Run The Default FiRa TWR Demo

Open two terminals. In both terminals, first enter the tools folder, activate
the environment, and set the variables.

macOS:

```bash
cd UWB_LAB/uwb-qorvo-tools
conda activate cosmos-ds
export UWB_TOOLS=$(pwd)
export PYTHONPATH="$UWB_TOOLS/lib/uwb-uci:$UWB_TOOLS/lib/uqt-utils:$UWB_TOOLS:$PYTHONPATH"
export CONTROLLER_PORT=/dev/cu.usbmodemD46FFE3655DD1
export CONTROLEE_PORT=/dev/cu.usbmodemE89E195B6C731
export GROUP_ID=10
```

Windows PowerShell:

```powershell
cd UWB_LAB\uwb-qorvo-tools
conda activate cosmos-ds
$env:UWB_TOOLS=(Get-Location).Path
$env:PYTHONPATH="$env:UWB_TOOLS\lib\uwb-uci;$env:UWB_TOOLS\lib\uqt-utils;$env:UWB_TOOLS;$env:PYTHONPATH"
$env:CONTROLLER_PORT="COM7"
$env:CONTROLEE_PORT="COM8"
$env:GROUP_ID="10"
```

Replace the two port values and group ID with your own values.

Terminal 1, start the controlee first.

macOS:

```bash
python scripts/fira/run_fira_twr/run_fira_twr.py \
  -p $CONTROLEE_PORT \
  --controlee \
  --preamble-idx $GROUP_ID \
  --aoa-report all-disabled \
  -t 35
```

Windows PowerShell:

```powershell
python scripts\fira\run_fira_twr\run_fira_twr.py `
  -p $env:CONTROLEE_PORT `
  --controlee `
  --preamble-idx $env:GROUP_ID `
  --aoa-report all-disabled `
  -t 35
```

Terminal 2, start the controller second.

macOS:

```bash
python scripts/fira/run_fira_twr/run_fira_twr.py \
  -p $CONTROLLER_PORT \
  --preamble-idx $GROUP_ID \
  --aoa-report all-disabled \
  -t 30
```

Windows PowerShell:

```powershell
python scripts\fira\run_fira_twr\run_fira_twr.py `
  -p $env:CONTROLLER_PORT `
  --preamble-idx $env:GROUP_ID `
  --aoa-report all-disabled `
  -t 30
```

The DWM3001CDK setup used in this lab does not use AoA, so AoA reporting is
disabled.

Successful output contains lines like:

```text
status: Ok (0x0)
distance: <value> cm
```

## 6. Configure The Update Rate

The update rate is controlled mainly by `--ranging-span`, which is the ranging
interval in milliseconds:

```text
FPS = 1000 / ranging-span
```

The default script uses:

```text
--slot-span 2400
--slots-per-rr 25
--ranging-span 200
```

This gives about:

```text
1000 / 200 = 5 FPS
```

For one controller and one controlee, this lab should run near 50 FPS. You
will configure the timing values yourself.

```text
slot duration (ms) = slot-span / 1200
minimum ranging-span = slot duration * slots-per-rr
target ranging-span = 1000 / target FPS
```

For this lab, use:

```text
target FPS = 50
slot-span = 2400
slots-per-rr = 6
```

TODO: compute a valid `RANGING_SPAN` value. It must be large enough for all
slots in the ranging round, and it should give about 50 FPS.

Set these variables in both terminals after you fill in the TODO.

macOS:

```bash
export SLOT_SPAN=2400
export SLOTS_PER_RR=6
export RANGING_SPAN=TODO_RANGING_SPAN
```

Windows PowerShell:

```powershell
$env:SLOT_SPAN="2400"
$env:SLOTS_PER_RR="6"
$env:RANGING_SPAN="TODO_RANGING_SPAN"
```

Again, these variables only exist in the current terminal. If you open a new
terminal, run them again.

## 7. Run 50 Hz Ranging And Save JSON

After setting the timing variables, run the experiment again and save the
generated JSON files.

macOS, create output folders:

```bash
mkdir -p "$UWB_TOOLS/lab_logs/group_${GROUP_ID}/controller"
mkdir -p "$UWB_TOOLS/lab_logs/group_${GROUP_ID}/controlee"
```

Windows PowerShell, create output folders:

```powershell
New-Item -ItemType Directory -Force -Path "$env:UWB_TOOLS\lab_logs\group_$($env:GROUP_ID)\controller" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:UWB_TOOLS\lab_logs\group_$($env:GROUP_ID)\controlee" | Out-Null
```

Terminal 1, controlee.

macOS:

```bash
cd "$UWB_TOOLS/lab_logs/group_${GROUP_ID}/controlee"
python "$UWB_TOOLS/scripts/fira/run_fira_twr/run_fira_twr.py" \
  -p $CONTROLEE_PORT \
  --controlee \
  --preamble-idx $GROUP_ID \
  --aoa-report all-disabled \
  --slot-span $SLOT_SPAN \
  --slots-per-rr $SLOTS_PER_RR \
  --ranging-span $RANGING_SPAN \
  --diag_dump \
  -t 35
```

Windows PowerShell:

```powershell
cd "$env:UWB_TOOLS\lab_logs\group_$($env:GROUP_ID)\controlee"
python "$env:UWB_TOOLS\scripts\fira\run_fira_twr\run_fira_twr.py" `
  -p $env:CONTROLEE_PORT `
  --controlee `
  --preamble-idx $env:GROUP_ID `
  --aoa-report all-disabled `
  --slot-span $env:SLOT_SPAN `
  --slots-per-rr $env:SLOTS_PER_RR `
  --ranging-span $env:RANGING_SPAN `
  --diag_dump `
  -t 35
```

Terminal 2, controller.

macOS:

```bash
cd "$UWB_TOOLS/lab_logs/group_${GROUP_ID}/controller"
python "$UWB_TOOLS/scripts/fira/run_fira_twr/run_fira_twr.py" \
  -p $CONTROLLER_PORT \
  --preamble-idx $GROUP_ID \
  --aoa-report all-disabled \
  --slot-span $SLOT_SPAN \
  --slots-per-rr $SLOTS_PER_RR \
  --ranging-span $RANGING_SPAN \
  --diag_dump \
  -t 30
```

Windows PowerShell:

```powershell
cd "$env:UWB_TOOLS\lab_logs\group_$($env:GROUP_ID)\controller"
python "$env:UWB_TOOLS\scripts\fira\run_fira_twr\run_fira_twr.py" `
  -p $env:CONTROLLER_PORT `
  --preamble-idx $env:GROUP_ID `
  --aoa-report all-disabled `
  --slot-span $env:SLOT_SPAN `
  --slots-per-rr $env:SLOTS_PER_RR `
  --ranging-span $env:RANGING_SPAN `
  --diag_dump `
  -t 30
```

`--diag_dump` writes `range_data_*.json` files into the folder where the command
is run. Save the controller JSON file for your ranging analysis. The controller
terminal should show a ranging interval close to the value you computed for
`RANGING_SPAN`.

## 8. Use The Ranging Experiment Wrapper

The manual two-terminal workflow is useful for learning the tool. For repeated
experiments, use `ranging_experiment_wrapper.py` from the main `UWB_LAB` folder.

The wrapper:

- resets both boards before the run unless `--skip-device-reset` is used
- starts the controlee first and controller second
- runs both Qorvo commands with the requested timing
- saves terminal logs for both boards
- parses distance measurements into `ranging_samples.csv`
- writes session metadata and a ranging summary
- optionally displays a live distance plot

Run a 30-second 50 Hz session with a live plot.

macOS:

```bash
cd UWB_LAB
conda activate cosmos-ds

python ranging_experiment_wrapper.py \
  --controller-port $CONTROLLER_PORT \
  --controlee-port $CONTROLEE_PORT \
  --group-id $GROUP_ID \
  --duration 30 \
  --fps 50 \
  --session-name group_${GROUP_ID}_range_demo \
  --visualize
```

Windows PowerShell:

```powershell
cd UWB_LAB
conda activate cosmos-ds

python ranging_experiment_wrapper.py `
  --controller-port $env:CONTROLLER_PORT `
  --controlee-port $env:CONTROLEE_PORT `
  --group-id $env:GROUP_ID `
  --duration 30 `
  --fps 50 `
  --session-name "group_$($env:GROUP_ID)_range_demo" `
  --visualize
```

The output folder will be under:

```text
UWB_LAB/sessions/group_<GROUP_ID>_range_demo/
```

To plot the ranging distribution, first finish the KDE TODO in
`analyze_ranging_results.py`, then run:

macOS:

```bash
python analyze_ranging_results.py \
  sessions/group_${GROUP_ID}_range_demo \
  --side controller \
  --show
```

Windows PowerShell:

```powershell
python analyze_ranging_results.py `
  "sessions\group_$($env:GROUP_ID)_range_demo" `
  --side controller `
  --show
```

## 9. Ranging Accuracy Experiment

Collect two controlled ranging trials:

1. Place the boards about 50 cm apart with clear line of sight.
2. Run the wrapper for 30 seconds and save the session.
3. Place the boards as far apart as possible up to about 2.5 m.
4. Run the wrapper for another 30 seconds and save the session.
5. Finish the TODO in `analyze_ranging_results.py`, then plot the KDE
   distribution for each session.

In your notes, report:

- target distance
- mean and median measured distance
- spread or stability of the measurements
- visible outliers or dropped measurements
- whether the long-distance trial is less stable than the 50 cm trial

## 10. Bimanual Activity Recognition

Final question:

```text
Can we recognize two-handed activities using only the distance between a
person's two wrists?
```

Place one DWM3001CDK board on each wrist or forearm. The classifier will use
only the inter-hand distance signal.

Possible activity classes:

- clapping
- opening and closing arms repeatedly
- passing an object between hands
- folding a towel
- stirring or mixing while holding a container
- putting on a glove
- tying or untying a simple knot
- resting or unrelated movement

Use short, repeatable gestures. For a first dataset, choose 3 or 4 classes and
collect multiple trials for each class.

## 11. Collect A Gesture Dataset

From `UWB_LAB`, run the collector.

macOS:

```bash
python collect_dataset.py \
  --controller-port $CONTROLLER_PORT \
  --controlee-port $CONTROLEE_PORT \
  --group-id $GROUP_ID \
  --collector student01 \
  --gesture clapping,t-arm,boxing,resting \
  --trials 8 \
  --trial-duration 3 \
  --pause 3 \
  --fps 50
```

Windows PowerShell:

```powershell
python collect_dataset.py `
  --controller-port $env:CONTROLLER_PORT `
  --controlee-port $env:CONTROLEE_PORT `
  --group-id $env:GROUP_ID `
  --collector student01 `
  --gesture clapping,open_close_arms,passing_object,resting `
  --trials 8 `
  --trial-duration 5 `
  --pause 3 `
  --fps 50
```

The collector starts one continuous ranging session, gives a countdown for each
trial, saves accepted trial windows, and asks whether to keep or redo each
trial.

Dataset structure:

```text
datasets/gesture_dataset_YYYYMMDD_HHMMSS/
  dataset_metadata.json
  trials.csv
  device_reset_log.txt
  final_device_reset_log.txt
  continuous_session/
  sessions/

sessions/range_student01_clapping_trial_001/
  trial_metadata.json
  session_metadata.json
  controller/ranging_samples.csv
  controlee/
```

`trials.csv` is the manifest used by the training script.

## 12. Combine Datasets From Multiple Students

After each student collects data, combine the dataset folders.

macOS:

```bash
python combine_datasets.py \
  datasets/gesture_dataset_YYYYMMDD_HHMMSS \
  datasets/gesture_dataset_YYYYMMDD_HHMMSS \
  --output datasets/combined_range_all_students
```

Windows PowerShell:

```powershell
python combine_datasets.py `
  datasets\gesture_dataset_YYYYMMDD_HHMMSS `
  datasets\gesture_dataset_YYYYMMDD_HHMMSS `
  --output datasets\combined_range_all_students
```

You can filter by collector or gesture:

macOS:

```bash
python combine_datasets.py \
  datasets/gesture_dataset_YYYYMMDD_HHMMSS \
  datasets/gesture_dataset_YYYYMMDD_HHMMSS \
  --collector student01 \
  --gesture clapping,resting \
  --output datasets/combined_subset
```

Windows PowerShell:

```powershell
python combine_datasets.py `
  datasets\gesture_dataset_YYYYMMDD_HHMMSS `
  datasets\gesture_dataset_YYYYMMDD_HHMMSS `
  --collector student01 `
  --gesture clapping,resting `
  --output datasets\combined_subset
```

## 13. Train A Range-Based Classifier

Train a baseline KNN model.

macOS:

```bash
python train.py datasets/gesture_dataset_YYYYMMDD_HHMMSS \
  --side controller \
  --classifier knn \
  --knn-neighbors 5
```

Windows PowerShell:

```powershell
python train.py datasets\gesture_dataset_YYYYMMDD_HHMMSS `
  --side controller `
  --classifier knn `
  --knn-neighbors 5
```

The starter code gives you two classifiers:

```bash
# KNN
python train.py datasets/combined_range_all_students \
  --side controller \
  --classifier knn \
  --knn-neighbors 5 \
  --knn-weights distance

# Linear SVM
python train.py datasets/combined_range_all_students \
  --side controller \
  --classifier svm_linear \
  --svm-c 1.0
```

For Windows PowerShell, use backticks instead of backslashes for multi-line
commands.

TODO: try at least one additional classifier by editing `build_classifier()` in
`train.py`. Useful references:

- scikit-learn classifier overview:
  https://scikit-learn.org/stable/supervised_learning.html
- Random Forest:
  https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.RandomForestClassifier.html
- Decision Tree:
  https://scikit-learn.org/stable/modules/generated/sklearn.tree.DecisionTreeClassifier.html
- Nonlinear SVM:
  https://scikit-learn.org/stable/modules/svm.html

Choose the proposed gesture-specific feature set with `--feature-set proposal`.
The starter proposal extractor currently returns the original baseline features
and 10 placeholder values. Your task is to replace the placeholders in
`extract_range_features_proposal()` with lightweight features that help separate
the activities. Train the same classifier with both feature sets to compare the
difference:

```bash
# Original compact baseline features
python train.py datasets/combined_range_all_students \
  --side controller \
  --feature-set baseline \
  --classifier knn

# Baseline + your proposal features
python train.py datasets/combined_range_all_students \
  --side controller \
  --feature-set proposal \
  --close-threshold-cm 25 \
  --classifier knn
```

Train on a combined dataset:

macOS:

```bash
python train.py datasets/combined_range_all_students --side controller
```

Windows PowerShell:

```powershell
python train.py datasets\combined_range_all_students --side controller
```

The range feature vector includes summary statistics and a resampled distance
shape over each trial window:

- count, mean, standard deviation, min, max, median
- quartiles, interquartile range, range
- first value, last value, total change
- average and maximum step change
- linear trend
- resampled distance trajectory

These are the raw baseline features. For your proposal features, look for
signals that are specific to two-handed motion, such as closeness, repeated
hand contacts, opening/closing trends, movement amount, direction changes, and
the spacing between valleys in the distance signal. Keep the features cheap
enough to compute during real-time evaluation.

To make the trajectory portion denser:

macOS:

```bash
python train.py datasets/combined_range_all_students \
  --side controller \
  --resample-points 40
```

Windows PowerShell:

```powershell
python train.py datasets\combined_range_all_students `
  --side controller `
  --resample-points 40
```

Training outputs:

```text
models/CLASSIFIER_range_YYYYMMDD_HHMMSS.joblib
models/CLASSIFIER_range_YYYYMMDD_HHMMSS_confusion_matrix.png
models/CLASSIFIER_range_YYYYMMDD_HHMMSS_summary.json
```

`CLASSIFIER` will be `knn`, `svm_linear`, or a method you add.

## 14. Evaluate Generalization Across Students

Random train/test splits can overestimate performance if the same student
appears in both sets. For a stronger test, hold out one collector:

macOS:

```bash
python train.py datasets/combined_range_all_students \
  --side controller \
  --test-collector student03
```

Windows PowerShell:

```powershell
python train.py datasets\combined_range_all_students `
  --side controller `
  --test-collector student03
```

Compare the random split accuracy and the leave-one-collector-out accuracy.

## 15. Run Real-Time Gesture Evaluation

Use the trained model with a live sliding window.

The real-time evaluator does not need a classifier flag. The `.joblib` file
stores the trained classifier, so the same command works for KNN, linear SVM,
and any other classifier you add. The evaluator calls the saved model's
`predict` method. If the model supports `predict_proba`, it also shows a
confidence value. The starter linear SVM model enables probability output. The
`.joblib` file also stores the feature set, so models trained with
`--feature-set proposal` automatically use the proposal feature extractor during
real-time evaluation.

By default, `eval_realtime.py` uses the timing stored in the trained model when
that metadata is available. Keep `--fps`, `--ranging-span`, `--slot-span`,
`--slots-per-rr`, and `--window-seconds` consistent with the data collection
settings unless you are intentionally testing a different setup. Add
`--strict-model-config` if you want the script to stop instead of only warning
when the evaluation timing differs from the model metadata.

By default, the evaluator shows raw predictions with `--vote-window 1`. After
you implement `majority_vote()` in `eval_realtime.py`, try `--vote-window 5` to
smooth the displayed result over recent prediction windows.

macOS:

```bash
python eval_realtime.py \
  --model datasets/combined_range_all_students/models/CLASSIFIER_range_YYYYMMDD_HHMMSS.joblib \
  --controller-port $CONTROLLER_PORT \
  --controlee-port $CONTROLEE_PORT \
  --group-id $GROUP_ID \
  --duration 60 \
  --step-seconds 0.5 \
  --vote-window 1 \
  --visualize
```

Windows PowerShell:

```powershell
python eval_realtime.py `
  --model datasets\combined_range_all_students\models\CLASSIFIER_range_YYYYMMDD_HHMMSS.joblib `
  --controller-port $env:CONTROLLER_PORT `
  --controlee-port $env:CONTROLEE_PORT `
  --group-id $env:GROUP_ID `
  --duration 60 `
  --step-seconds 0.5 `
  --vote-window 1 `
  --visualize
```

The evaluator starts a live ranging session, keeps a recent distance window,
extracts the same range features used during training, and writes predictions
to:

```text
sessions/eval_group_<GROUP_ID>_<timestamp>/realtime_predictions.csv
```

TODO: implement majority voting in `eval_realtime.py`. The function should take
recent raw predictions, return the most common class, report a vote fraction,
and handle ties in a reasonable way.

## 16. Suggested Student TODOs

Use the working pipeline above, then improve it:

- compute and verify a valid 50 Hz ranging configuration
- implement the KDE plot in `analyze_ranging_results.py`
- design proposal features in `extract_range_features_proposal()` inside
  `uwb_lab_common.py`
- add and compare at least one classifier beyond KNN and linear SVM
- implement majority voting in `eval_realtime.py`
- compare different trial lengths and sliding-window lengths
- evaluate random split versus leave-one-collector-out split
- inspect the confusion matrix and identify which activities are confused
- add a better visualization for distance over time during each collected trial
- decide which activities are realistic to classify using distance only

## Troubleshooting

If ranging does not start:

- confirm both boards are plugged in
- confirm each terminal has the correct terminal variables
- confirm no other terminal is using the same serial port
- restart from the controlee first, then the controller
- unplug and reconnect both boards if the serial ports stop responding

If the update rate is wrong:

- check that `SLOT_SPAN`, `SLOTS_PER_RR`, and `RANGING_SPAN` were set in both
  terminals
- confirm the controller prints a ranging interval close to your computed
  `RANGING_SPAN`
- remember that terminal variables do not carry over to a new terminal

If plots fail to open:

- make sure `conda activate cosmos-ds` is active
- install the plotting dependencies in that environment
- rerun the command after the environment is fixed
