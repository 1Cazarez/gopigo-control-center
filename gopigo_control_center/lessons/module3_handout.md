# CSC 4120/6120 – Introduction to Robotics: Module 3 Lab Handout

## Overview

All source code for the GoPiGo3 robot is available on its GitHub page: https://github.com/DexterInd/GoPiGo3

Note: to change the robot's SSID/name, follow this guide: https://www.dexterindustries.com/howto/change-the-hostname-of-your-pi/

### Planned Path Robot Moving

## Exercise 1 – Learn the easygopigo3 Library

The GoPiGo3 robot has its own dedicated library, called easygopigo3. Familiarize yourself with the library in this exercise. We can move the robot based on time, distance and even wheel rotation (say one wheel rotation, 360 degrees).

From the Dashboard, go to Learn > Lessons in Python.

Go through this exercise: `1_Moving_Around.ipynb`

Result: drive forward 10 cm, followed by 10 inches.

Try the methods below, as suggested in the script, by adding new cells along the way:

```
my_gopigo.forward()
my_gopigo.backward()
my_gopigo.stop()
my_gopigo.right()
my_gopigo.left()
my_gopigo.turn_degrees(90)
my_gopigo.turn_degrees(-90)
```

## Exercise 2 – Motor Encoders

### 2A. Motor Encoders (Motor_Encoder.py)

Create a folder in the Home directory: press the new folder icon, right-click the new untitled folder and rename it "Module3". Double-click to enter the folder.

Click the third icon (the upload icon) below the menu bar. Upload the file `Motor_Encoder.py` from Module 3 -> Exercise 2 of the support files.

In the terminal, type:

```
cd Module3
python3 Motor_Encoder.py
```

This code is an example of reading the GoPiGo3 motors' encoders.

Results: when you run this program, the GoPiGo3 motors' positions are printed.

### 2B. Motor Follow (Motor_Follow.py)

Create a folder in the Home directory: press the new folder icon, right-click the new untitled folder and rename it "Module3". Double-click to enter the folder.

Click the third icon (the upload icon) below the menu bar. Upload the file `Motor_Follow.py` from Module 3 -> Exercise 2 of the support files.

In the terminal, type:

```
cd Module3
python3 Motor_Follow.py
```

This code is an example of controlling the GoPiGo3 motors.

Results: when you run this program, the GoPiGo3 motors rotate back and forth and the motors' positions are printed.

### 2C. Motor Position (Motor_Position.py)

Create a folder in the Home directory: press the new folder icon, right-click the new untitled folder and rename it "Module3". Double-click to enter the folder.

Click the third icon (the upload icon) below the menu bar. Upload the file `Motor_Position.py` from Module 3 -> Exercise 2 of the support files.

In the terminal, type:

```
cd Module3
python3 Motor_Position.py
```

This code is an example of controlling the GoPiGo3 motors. In a loop, the robot moves two wheel rotations forward followed by two wheel rotations backward.

Results: when you run this program, the GoPiGo3 motors rotate back and forth.

Try printing the motors' encoder positions to see how they change during forward and backward wheel rotation (the convention is + forward, - backward). Add the line below to print them, as seen in 2A:

```
print("Encoder L: %6d R: %6d" % (GPG.get_motor_encoder(GPG.MOTOR_LEFT),
                                 GPG.get_motor_encoder(GPG.MOTOR_RIGHT)))
```

### 2D. Motor Speed (Motor_Speed.py)

Create a folder in the Home directory: press the new folder icon, right-click the new untitled folder and rename it "Module3". Double-click to enter the folder.

Click the third icon (the upload icon) below the menu bar. Upload the file `Motor_Speed.py` from Module 3 -> Exercise 2 of the support files.

In the terminal, type:

```
cd Module3
python3 Motor_Speed.py
```

This code is an example of controlling the GoPiGo3 motors.

Results: when you run this program, manually rotate the left motor to control the speed of the right motor.

### 2E. Motor Turn (Motor_Turn.py)

Create a folder in the Home directory: press the new folder icon, right-click the new untitled folder and rename it "Module3". Double-click to enter the folder.

Click the third icon (the upload icon) below the menu bar. Upload the file `Motor_Turn.py` from Module 3 -> Exercise 2 of the support files.

In the terminal, type:

```
cd Module3
python3 Motor_Turn.py
```

This code is an example of making the GoPiGo3 turn accurately.

Results: when you run this program, the GoPiGo3 should turn 90 degrees to the right, 180 to the left, and then 90 to the right, ending where it started.

Try different degrees of wheel rotation and speeds, which can be passed as arguments as in the snippet below:

```
TurnDegrees(arg1, arg2)
# turn arg1 degrees to the right, at a wheel speed of arg2 degrees per second
# + arg1 is the convention for a right turn; - arg1 is the convention for a left turn
```

## Exercise 3 – Keyboard Robot Control

Go through the program below to move the robot around using Python methods.

Go to: Home > GoPiGo3-Projects

To run Python scripts, open a terminal from the launcher: File > New > Terminal

Once in the terminal, go to the directory to run the script from. In the terminal, type:

```
cd GoPiGo3-Projects/BasicRobotControl
python3 run_this.py
```

Result: you should be able to control the robot's movements with key presses, as directed by the instructions in the terminal. You should also be able to control the blinkers and the robot's eyes.

(The original handout shows a screenshot of the console here.)

Refer to the videos in Module 3 for the robot in action, performing various movement and blinking functions.

## Exercise 4 – Mouse Robot Control

Connect a wireless mouse to the Raspberry Pi's USB port. You'll be able to control your GoPiGo3 robot with the wireless mouse, either by mouse clicks or by mouse movements.

In the terminal, type:

```
cd GoPiGo3-Projects/MouseControl
python3 mouse_control_robot.py
```

Result:

- Choice 1: control your GoPiGo3 robot with the wireless mouse by mouse clicks.
- Choice 2: control your GoPiGo3 robot with the wireless mouse by mouse movements.

## Assignment Problems for Demonstration/Submission

### Problem 1

Autonomously move the robot along the perimeter of a square path of 0.5 m x 0.5 m. If the robot senses an obstacle within 25 cm on its way, it must stop, rotate by 180 degrees and trace the path in the other direction. Store the path trace in a file.

Expected output: create a folder named Module 3. The path trace should be saved in a file named `problem1_pathtrace.csv`. Each row of the path trace should have 3 columns: index (row number), wheel encoder value, distance value from the distance sensor. The path trace must be collected at at least 2 samples/second.

Bonus (2 pts): the entire episode must be streamed using the remote camera option. (Hint: https://github.com/DexterInd/GoPiGo3/tree/master/Projects/RemoteCameraRobot)

Hints:

1. Logging to the console or a file in Python: https://docs.python.org/3/howto/logging.html

### Problem 2

Autonomously move the robot forward along the four cardinal directions. The path is: turn towards North → move 100 cm → turn towards East → move 100 cm → turn towards South → move 100 cm → turn towards West → move 100 cm → turn towards North and REPEAT. If it detects an obstacle within 30 cm along the path, it MUST AVOID THE OBSTACLE and MOVE IN ANY OTHER DIRECTION SO AS TO NOT HIT THE OBSTACLE. Store the path trace in a file.

Expected output: save the path trace as `problem2_pathtrace.csv` in the Module 3 folder. Each row of the path trace should have 4 columns: index (row number), wheel encoder value, distance value from the distance sensor, tracing path direction. The path trace must be collected at at least 2 samples/second.

Note: the tracing path direction can be noted as N, S, E, W, representing the four cardinal directions.

### Problem 3

The entire episode must be captured as a video, and the video must be downloaded onto the laptop and shown. It is OKAY to have a pre-run demo already saved, but we will also check for the video creation in real time.

### Problem 4

Additional question for GRAD STUDENTS ONLY: the robot must circle around the obstacle, get back to its original forward path and continue moving. (Bonus 5 pts if demonstrated by UG students.)
