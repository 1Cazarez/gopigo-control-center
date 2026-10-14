# CSC 4120/6120 – Introduction to Robotics: Module 3 Study Guide

## Introduction

This manual provides a comprehensive guide for Module 3 of the GoPiGo3 Robotics curriculum. It consolidates exercises, assignments, and detailed step-by-step procedures into a single reference document.

By following this guide, students will:

- Learn and apply the easygopigo3 library for robot control.
- Understand motor encoders through practical experiments.
- Control the robot using keyboard and mouse inputs.
- Complete assignments involving autonomous navigation, obstacle avoidance, and logging.
- Record outputs and verify performance against checklists.

Assignment solutions (code) are not included. Instead, requirements, execution steps, and verification methods are provided.

## Prerequisites & Setup

Before starting Module 3, ensure you have the following:

1. GoPiGo3 robot assembled with tight wheels and working encoders.
2. Raspberry Pi with DexterOS or Raspbian for Robots installed.
3. Python 3 installed with the easygopigo3 library.
4. Distance sensor connected firmly to the correct port.
5. Wi‑Fi or Ethernet connection; know your robot hostname.
6. Access to the GoPiGo dashboard (http://mygopigo.com) or an SSH terminal.
7. Fully charged battery pack.
8. Safety: clear area for movement; keep a hand near the power switch.

## Helpful Links

- GoPiGo3 GitHub repository: https://github.com/DexterInd/GoPiGo3
- Change Raspberry Pi hostname/SSID: https://www.dexterindustries.com/howto/change-the-hostname-of-your-pi/
- Remote Camera Robot Project: https://github.com/DexterInd/GoPiGo3/tree/master/Projects/RemoteCameraRobot

## Exercise 1 – Learning the easygopigo3 Library

Objective: Familiarize yourself with the basic movement commands available in the easygopigo3 library.

Steps:

1. Open Jupyter Notebook → Learn → Lessons in Python → `1_Moving_Around.ipynb`.
2. Import and initialize the robot:

```
import easygopigo3 as egpg
g = egpg.EasyGoPiGo3()
```

3. Test commands in separate cells: `g.forward()`, `g.backward()`, `g.stop()`, `g.right()`, `g.left()`
4. Practice rotations: `g.turn_degrees(90)`, `g.turn_degrees(-90)`
5. Verify the robot moves straight, rotates accurately, and stops safely.

## Exercise 2 – Motor Encoders

Objective: Learn how to use motor encoders for position, speed, and turn control.

### 2A. Motor Encoders (Motor_Encoder.py)

- Upload `Motor_Encoder.py` into `~/Module3`.
- Run: `cd ~/Module3; python3 Motor_Encoder.py`
- Observe encoder values (degrees) as the wheels rotate.

### 2B. Motor Follow (Motor_Follow.py)

- Upload `Motor_Follow.py` into `~/Module3`.
- Run: `python3 Motor_Follow.py`
- Motors move back and forth; encoder values display.

### 2C. Motor Position (Motor_Position.py)

- Upload `Motor_Position.py` into `~/Module3`.
- Run: `python3 Motor_Position.py`
- Robot moves forward two wheel rotations, then backward two.

### 2D. Motor Speed (Motor_Speed.py)

- Upload `Motor_Speed.py` into `~/Module3`.
- Run: `python3 Motor_Speed.py`
- Manually rotating the left motor adjusts the right motor's speed.

### 2E. Motor Turn (Motor_Turn.py)

- Upload `Motor_Turn.py` into `~/Module3`.
- Run: `python3 Motor_Turn.py`
- Robot turns 90° right, 180° left, then 90° right, returning to its starting orientation.

## Exercise 3 – Keyboard Control

Objective: Control the robot via keyboard keys to practice precise movements.

Steps:

1. Open a terminal.
2. Navigate: `cd GoPiGo3-Projects/BasicRobotControl`
3. Run: `python3 run_this.py`
4. Use keys to control movement (displayed in the console).

### Key Bindings

- `w`: forward, `s`: backward
- `a`: left, `d`: right
- `SPACE`: stop
- `F1`: forward 10 cm, `F2`: forward 10 inches, `F3`: forward 360°
- `1` / `2`: blinkers toggle
- `8` / `9`: eyes toggle
- `ESC`: exit

## Exercise 4 – Mouse Control

Objective: Control the robot with a wireless mouse.

Steps:

1. Connect a wireless mouse receiver to a Pi USB port.
2. Navigate: `cd GoPiGo3-Projects/MouseControl`
3. Run: `python3 mouse_control_robot.py`
4. Select a control mode:
    - Choice 1: mouse clicks
    - Choice 2: mouse movement
5. Verify: the robot responds to mouse input smoothly.

## Assignment Problems

### Problem 1

Task: Robot traces a 0.5 m square. If an obstacle is ≤25 cm away, stop, rotate 180°, and trace in the opposite direction.

Requirements:

- Log the path trace into `problem1_pathtrace.csv`.
- Columns: index, encoder value, distance (cm).
- Sampling rate: ≥2 Hz.
- Bonus: implement camera streaming.

### Problem 2

Task: Move 100 cm North → East → South → West → repeat. If an obstacle is ≤30 cm away, avoid it and continue.

Requirements:

- Log the path trace into `problem2_pathtrace.csv`.
- Columns: index, encoder value, distance (cm), direction (N/E/S/W).
- Sampling rate: ≥2 Hz.

### Problem 3

Task: Capture a video of the entire run and download it to a laptop. A pre-recorded demo is acceptable, but live capture may be verified.

### Problem 4

Task: Circle around an obstacle and return to the original path.
