# System Architecture Overview

This document provides an overview of the system architecture for the OpenScan3 application, which is designed to control the OpenScan photogrammetry scanner using FastAPI and a Raspberry Pi.

The architecture follows in generally the MVC (Model-View-Controller) pattern slightly adapted to fit the needs of an embedded application. Note that the controllers are responsible for the actual business logic and that models and configs are separated.

## Main Components

1. **FastAPI Application**
   - Acts as the main server handling HTTP requests.
   - Provides RESTful API endpoints for controlling the scanner.

2. **OpenScan Hardware Device**
   - usually a Raspberry Pi with a custom shield for connecting various hardware components.
   - Serves as the hardware platform for the scanner.
   - Interfaces with various peripherals (cameras, motors, lights, etc.).

## FastAPI Application Structure

1. **Routers**
   - Routers in `app/routers` handle incoming API requests.

2. **Controllers**
   - Controllers in `app/controllers` implement the business logic.
   - Settings are managed at runtime within `app/controllers/settings.py`
   - (TODO: Detection and initialization of hardware will be managed by the Device Controller within `app/controllers/hardware/device.py`, which is also capable of managing configuration profiles)

   2.1. **Hardware Controllers**
      - Located in `app/controllers/hardware` control the hardware components of the scanner.
      - Abstracts the hardware details from the application logic.
      - Supports multiple hardware configurations.
      - Hardware is divided into three categories: stateful hardware (like motors and cameras), switchable hardware (like lights), and event hardware (like buttons and simple sensors).
         - HardwareControllers inherit from the according HardwareInterface class in `app/controllers/hardware/interfaces.py`.
         - HardwareControllers instantiate the settings manager in `app/controllers/settings.py` to manage and update settings.

   2.2. **Service Controllers**
      - Controllers in `app/controllers/services` handle the business logic of the scanner.
      - Examples: Managing scan projects and scan procedures.

3. **Configuration Management**
   - Located in `app/config`.
   - Manages settings for different components.

4. **Models and Data Structures**
   - Defined in `app/models`.
   - Represents the data entities and their relationships.



## Interactions

- The FastAPI application receives requests from clients and routes them to the appropriate controllers via routers.
- Controllers interact with the hardware abstraction layer to perform operations on the scanner.

This architecture is designed to be modular and extensible, allowing for easy integration of new hardware components and features.