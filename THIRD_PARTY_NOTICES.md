# Third-party notices

This repository's own code is under the [MIT License](LICENSE). It also includes or is
derived from the following third-party work.

## Blockly

The Block Builder tab bundles Blockly 13.3.0 (Google), licensed under the Apache License 2.0. The
full license text is in [`gopigo_control_center/web/blockly/LICENSE`](gopigo_control_center/web/blockly/LICENSE).

- Source: <https://github.com/google/blockly>

## picamera2 MJPEG streaming example

The camera streaming server in
[`gopigo_control_center/remote_scripts.py`](gopigo_control_center/remote_scripts.py)
(`CAMERA_STREAM_SCRIPT`) is adapted from the `examples/mjpeg_server.py` example in picamera2. The
example's own header says it was in turn mostly copied from an older picamera recipe. picamera2 is
licensed as follows.

- Source: <https://github.com/raspberrypi/picamera2>

```
BSD 2-Clause License

Copyright (c) 2021, Raspberry Pi
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```
