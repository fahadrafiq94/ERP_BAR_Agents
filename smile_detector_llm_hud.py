import collections
import os
import time
import urllib.request
import queue
from collections import deque
import cv2
import numpy as np
import mediapipe as mp


from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision


# Import the class that starts the LLM agent workflow.
# Also import thought_queue, which is used to receive messages
# from the background LLM thread and display them in the HUD.
from llm_agent_engine import AgentReasoningRunner, thought_queue
from odoo_connector import DEFAULT_PRODUCT_TEMPLATE_ID

# ============================================================================
# CONFIGURATION
# ============================================================================

# Name/location of the MediaPipe face-landmark model file.
MODEL_PATH = "face_landmarker.task"

# Internet location from which the model can be downloaded
# if it does not already exist on the computer.
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task"

# Minimum smile confidence required to consider the person smiling.
# The MediaPipe smile score is approximately between 0.0 and 1.0.
SMILE_THRESHOLD = 0.75

# Number of recent frames used to calculate the average smile score.
# This prevents one noisy frame from immediately triggering the agent.
SMOOTHING_FRAMES = 5

# Camera index.
# 0 normally means the computer's default webcam.
CAM_INDEX = 0

# Minimum amount of time between two agent triggers.
# Without this cooldown, the agent could be triggered continuously
# while somebody remains smiling.
TRIGGER_COOLDOWN = 15.0  # Seconds between triggers

# ============================================================================
# MAKE SURE THE MEDIAPIPE MODEL EXISTS
# ============================================================================

def ensure_model():
    # Check whether the model file already exists.
    if not os.path.exists(MODEL_PATH):
        # Tell the user that the model is being downloaded.
        print("Downloading landmark model...")
        # Download the model from MODEL_URL.
        # Save it locally using the filename stored in MODEL_PATH.
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)

# ============================================================================
# CREATE THE MEDIAPIPE FACE LANDMARKER
# ============================================================================

def build_landmarker():
    # Tell MediaPipe where the trained face-landmark model is located.
    base_options = mp_python.BaseOptions(model_asset_path=MODEL_PATH)
    # Configure the FaceLandmarker.
    options = vision.FaceLandmarkerOptions(
        # Use the model configuration created above.
        base_options=base_options,
         # Ask MediaPipe to return facial expression/blendshape scores.
        # These scores include mouthSmileLeft and mouthSmileRight.
        output_face_blendshapes=True,
        # Detect only one face.
        # Our application is designed for one customer at a time.
        num_faces=1,
        # Tell MediaPipe that we are processing a video stream.
        running_mode=vision.RunningMode.VIDEO,
    )
    # Create and return the actual FaceLandmarker object.
    return vision.FaceLandmarker.create_from_options(options)

# ============================================================================
# CALCULATE THE SMILE SCORE
# ============================================================================

def get_smile_score(blendshapes):
    # Start with zero for both sides of the mouth.
    left = right = 0.0
    # Look through every facial-expression category returned by MediaPipe.
    for category in blendshapes:
         # Check for the left-side smile expression
        if category.category_name == "mouthSmileLeft":
            # Store the confidence score for the left side.
            left = category.score
        # Check for the right-side smile expression
        elif category.category_name == "mouthSmileRight":
            # Store the confidence score for the right side.
            right = category.score
    # Average the left and right smile scores.
    #
    # Example:
    # left  = 0.80
    # right = 0.90
    #
    # Result:
    # (0.80 + 0.90) / 2 = 0.85
    return (left + right) / 2.0


# ============================================================================
# MAIN APPLICATION
# ============================================================================

def main():
    # Make sure the MediaPipe model exists.
    # If it does not exist, it will be downloaded.
    ensure_model()
    # Create the MediaPipe face detector/landmarker.
    landmarker = build_landmarker()
    # Create the object that will run the LLM agent.
    # AgentReasoningRunner comes from llm_agent_engine.py.
    agent_runner = AgentReasoningRunner()

    # Open the webcam.
    # CAM_INDEX = 0 normally means the default camera.
    cap = cv2.VideoCapture(CAM_INDEX)
    # If the webcam is not found, print an error message and exit.
    if not cap.isOpened():
        print("Error: Camera not found.")
        # Exit the program.
        return

    # =========================================================================
    # SMILE SCORE HISTORY
    # =========================================================================

    # Create a dictionary containing a small history of smile scores.
    # defaultdict automatically creates a new deque when a new face index is encountered.
    # deque(maxlen=SMOOTHING_FRAMES) means we only keep the latest 5 scores by default.
    history = collections.defaultdict(lambda: collections.deque(maxlen=SMOOTHING_FRAMES))
    # Store the start time of the application.  
    start_time = time.time()
    # Store the time of the last agent trigger.
    # 0.0 means that no trigger has happened yet.
    last_trigger_time = 0.0

    # =========================================================================
    # HUD LOG CONFIGURATION
    # =========================================================================

    # Completed messages already fully typed on screen.
    hud_logs = ["--- SYSTEM A: AGENT REASONING BRAIN READY ---"]

    # Messages waiting to be rendered with the typewriter animation.
    pending_hud_logs = deque()

    # Current message being typed from left to right.
    active_hud_log = None
    active_hud_chars = 0.0
    last_typewriter_update = time.time()

    # Typewriter speed in characters per second.
    # Increase this if you want the narration to appear faster.
    TYPEWRITER_CHARS_PER_SECOND = 95.0

    # Keep a large history; the renderer automatically scrolls to the newest
    # wrapped lines that fit inside the visible HUD panel.
    MAX_HUD_MESSAGES = 120

    # Once the invoice has been posted, freeze the HUD on that successful
    # invoice message. Any later supervisor/final/error/debug messages from
    # the completed transaction are discarded until the next smile starts a
    # fresh transaction.
    hud_frozen_after_invoice = False

    # Tell the user that the system has started.
    print("System A Engine Active. Press 'q' in the window to exit.")

    # =========================================================================
    # MAIN CAMERA LOOP
    # =========================================================================

    # Keep processing frames until the user presses 'q' or the camera stops providing frames.
    while True:
        # Read one frame from the camera.
        # ok: True if the frame was successfully captured.
        # frame: The actual camera image.
        ok, frame = cap.read()
        # If the frame was not successfully captured, break the loop.
        if not ok:
            break

        # =========================================================================
        # PREPARE CAMERA FRAME
        # =========================================================================

        # Flip the image horizontally.
        # This makes the camera behave like a mirror.
        frame = cv2.flip(frame, 1)
        # Get the height and width of the camera image.
        # frame.shape normally returns: height, width, channels
        # We do not need the number of channels here, so "_" is used for that value.
        h, w, _ = frame.shape

        # =========================================================================
        # 1. READ MESSAGES FROM THE LLM AGENT
        # =========================================================================

        # The LLM runs in another/background thread and places complete
        # narration messages into thought_queue. We first move them into a
        # local pending queue. They are then animated character-by-character.
        #
        # IMPORTANT: after a successful invoice result is received, the HUD
        # freezes for this transaction. Messages produced after the invoice
        # (including final summaries, warnings, RPC tracebacks, etc.) are
        # intentionally discarded until the next smile starts a new run.
        while not thought_queue.empty():
            try:
                incoming_log = thought_queue.get_nowait()
            except queue.Empty:
                break

            if hud_frozen_after_invoice:
                # Transaction is visually complete. Silently discard anything
                # else generated by the finished background workflow.
                continue

            pending_hud_logs.append(incoming_log)

            if "[INVOICE RESULT]" in incoming_log:
                hud_frozen_after_invoice = True

        # -----------------------------------------------------------------
        # TYPEWRITER ANIMATION
        # -----------------------------------------------------------------
        # Start the next pending message only after the previous message has
        # finished typing. This gives the HUD a real left-to-right live stream.
        if active_hud_log is None and pending_hud_logs:
            active_hud_log = pending_hud_logs.popleft()
            active_hud_chars = 0.0
            last_typewriter_update = current_frame_time = time.time()
        else:
            current_frame_time = time.time()

        if active_hud_log is not None:
            elapsed = max(0.0, current_frame_time - last_typewriter_update)
            active_hud_chars += elapsed * TYPEWRITER_CHARS_PER_SECOND
            last_typewriter_update = current_frame_time

            if active_hud_chars >= len(active_hud_log):
                hud_logs.append(active_hud_log)
                if len(hud_logs) > MAX_HUD_MESSAGES:
                    hud_logs = hud_logs[-MAX_HUD_MESSAGES:]
                active_hud_log = None
                active_hud_chars = 0.0

        # =========================================================================
        # 2. PROCESS THE CAMERA FRAME WITH MEDIAPIPE
        # =========================================================================

        # OpenCV normally gives us a BGR image.
        # MediaPipe expects RGB.
        # Therefore convert BGR -> RGB.
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
         # Convert the NumPy image into a MediaPipe Image object.
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        # Calculate how many milliseconds have passed since the program started.
        # MediaPipe's VIDEO mode requires a timestamp for each frame.
        timestamp_ms = int((time.time() - start_time) * 1000)
        # Send the current frame to MediaPipe.
        # The result contains:
        # - face landmarks
        # - face blendshapes
        # - other face information
        result = landmarker.detect_for_video(mp_image, timestamp_ms)
        # Store the current time.
        # This is used to calculate whether the trigger cooldown has expired.
        current_time = time.time()


        # =========================================================================
        # CHECK WHETHER A FACE WAS DETECTED
        # =========================================================================

        # result.face_landmarks contains the landmarks for detected faces.
        # If it contains anything, at least one face was detected.
        if result.face_landmarks:
            # Pair the face landmarks with their corresponding facial blendshape information.
            # enumerate() also gives us the face index.
            for i, (landmarks, blendshapes) in enumerate(zip(result.face_landmarks, result.face_blendshapes)):
                
                # =================================================================
                # CREATE FACE BOUNDING BOX
                # =================================================================

                # MediaPipe landmark x coordinates are normalized.
                # For example: x = 0.5 means the landmark is approximately halfway across the image.
                # Multiply by image width to get pixel coordinates.
                xs = [p.x * w for p in landmarks]
                # Do the same for y coordinates.
                ys = [p.y * h for p in landmarks]
                # Find the smallest x and y coordinates. These form the top-left corner of the face rectangle.
                x1, y1, x2, y2 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))


                # =================================================================
                # CALCULATE SMILE SCORE
                # =================================================================

                # Extract the left/right mouth smile scores and convert them into one overall score.
                score = get_smile_score(blendshapes)
                # Store this score in the history for this face.
                history[i].append(score)
                # Calculate the average of the recent scores. This makes the detector more stable.
                smooth_score = sum(history[i]) / len(history[i])
                # Decide whether the person is smiling.
                # True  -> smile score is above 0.75
                # False -> smile score is 0.75 or below
                smiling = smooth_score > SMILE_THRESHOLD

                # Choose the display color.
                # OpenCV uses BGR: Green = (0, 255, 0), Red = (0, 0, 255)
                color = (0, 255, 0) if smiling else (0, 0, 255)
                
                # =================================================================
                # DRAW FACE RECTANGLE
                # =================================================================

                # Draw a rectangle around the detected face.
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

                # =================================================================
                # DISPLAY SMILE SCORE
                # =================================================================

                # Draw the current smile score above the face.
                # :.2f means: display the score with 2 decimal places.
                # max(0, y1 - 10) prevents the text from going outside the top of the image.
                cv2.putText(frame, f"Smile: {smooth_score:.2f}", (x1, max(0, y1 - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

                # =================================================================
                # TRIGGER THE AGENT WORKFLOW
                # =================================================================
                
                # The agent is triggered only when:
                # 1. The person is smiling. AND
                # 2. At least TRIGGER_COOLDOWN seconds have passed since the previous trigger.
                # This prevents the agent from being triggered on every camera frame.
                if smiling and (current_time - last_trigger_time > TRIGGER_COOLDOWN):
                    # Remember the time at which this trigger happened.
                    last_trigger_time = current_time

                    # A new customer transaction starts a completely fresh HUD
                    # stream. Unlock the display, discard any stale background
                    # messages from the previous transaction, and reset the
                    # typewriter state before starting the new agent workflow.
                    hud_frozen_after_invoice = False
                    hud_logs = ["--- NEW CUSTOMER TRANSACTION ---"]
                    pending_hud_logs.clear()
                    active_hud_log = None
                    active_hud_chars = 0.0
                    last_typewriter_update = time.time()

                    while not thought_queue.empty():
                        try:
                            thought_queue.get_nowait()
                        except queue.Empty:
                            break

                    # Create the instruction that will be sent to the LLM Supervisor Agent.
                    # The instruction tells the agent:
                    # - a customer smiled
                    # - how confident the smile detector was
                    # - create a Sale Order
                    # - configured product template ID
                    # - generic walk-in customer configured in .env
                    # - quantity = 1
                    # - check inventory
                    # - automatically purchase stock if necessary
                    prompt = (
                        f"A customer smiled into the camera with confidence {smooth_score:.2f}! "
                        f"Process one anonymous walk-in retail transaction for quantity 1. "
                        f"Use Product Template ID {DEFAULT_PRODUCT_TEMPLATE_ID}. "
                        f"Use the configured Walk-In Customer and configured warehouse. "
                        f"Check/replenish stock first, then create the sale, deliver it, and post the invoice."
                    )
                    # Start the LLM workflow asynchronously.
                    # This is very important: The camera does NOT wait for the LLM.
                    # The LLM workflow runs in a background thread while this camera loop continues processing frames.
                    agent_runner.run_agent_async(prompt)

        # =========================================================================
        # 3. CREATE THE DUAL-PANE HUD
        # =========================================================================

        # Width of the right-side HUD panel.
        hud_width = 700
        # Create a blank image large enough to contain: Camera width + HUD width
        # The image has 3 color channels because it is a BGR image.
        combined_screen = np.zeros((h, w + hud_width, 3), dtype=np.uint8)
        
        # =========================================================================
        # PUT CAMERA FEED ON THE LEFT
        # =========================================================================

         # Copy the camera frame into the left side of the combined screen.
        combined_screen[0:h, 0:w] = frame

        # =========================================================================
        # CREATE RIGHT-SIDE HUD PANEL
        # =========================================================================

        # Select the right side of the combined image. hud_panel is a view into combined_screen.
        hud_panel = combined_screen[0:h, w:w + hud_width]
        hud_panel[:] = (20, 20, 25) # Fill the HUD with a dark background.
        
        # =========================================================================
        # DRAW HUD TITLE BAR
        # =========================================================================

        # Draw a dark rectangle at the top of the HUD. -1 means the rectangle should be filled.
        cv2.rectangle(hud_panel, (0, 0), (hud_width, 45), (35, 35, 45), -1)
         # Write the HUD title.
        cv2.putText(hud_panel, "MULTI-AGENT DECISION STREAM", (15, 27),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
        cv2.putText(hud_panel, "LIVE TYPE  |  AUTO-SCROLL", (425, 27),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (160, 160, 160), 1, cv2.LINE_AA)
        # Draw a horizontal line underneath the title.
        cv2.line(hud_panel, (0, 45), (hud_width, 45), (0, 255, 255), 1)

        # =========================================================================
        # DISPLAY AGENT LOGS - TYPEWRITER + AUTO-SCROLL
        # =========================================================================

        def get_log_color(log_text):
            """Return a distinct BGR color for each narrated agent."""
            if "[SUPERVISOR THINK]" in log_text or "[SUPERVISOR →" in log_text:
                return (0, 220, 255)
            if "[INVENTORY AGENT THINK]" in log_text or "[INVENTORY AGENT →" in log_text:
                return (255, 200, 100)
            if "[SALES AGENT THINK]" in log_text or "[SALES AGENT →" in log_text:
                return (255, 180, 255)
            if "[DISPENSER AGENT THINK]" in log_text or "[DISPENSER AGENT →" in log_text:
                return (180, 255, 180)
            if "[INVOICE AGENT THINK]" in log_text or "[INVOICE AGENT →" in log_text:
                return (220, 220, 120)
            if "[OBSERVATION]" in log_text:
                return (200, 255, 200)
            if "[FINAL]" in log_text:
                return (0, 255, 0)
            if "[ERROR]" in log_text:
                return (0, 0, 255)
            if "[BUSY]" in log_text:
                return (0, 165, 255)
            return (190, 190, 190)

        def wrap_log(log_text, max_chars=78):
            """Wrap one message on word boundaries for the HUD width."""
            if not log_text:
                return [""]

            words = log_text.split()
            if not words:
                return [log_text]

            wrapped = []
            current = ""
            for word in words:
                candidate = f"{current} {word}".strip()
                if len(candidate) <= max_chars:
                    current = candidate
                else:
                    if current:
                        wrapped.append(current)
                    # Very long tokens are split so they cannot overflow the HUD.
                    while len(word) > max_chars:
                        wrapped.append(word[:max_chars])
                        word = word[max_chars:]
                    current = word
            if current:
                wrapped.append(current)
            return wrapped or [""]

        # Build display rows from all completed messages plus the currently
        # typing partial message. Each wrapped row remembers its parent message
        # color so auto-scroll does not lose agent identity.
        display_rows = []
        for complete_log in hud_logs:
            color = get_log_color(complete_log)
            for wrapped_line in wrap_log(complete_log):
                display_rows.append((wrapped_line, color, False))

        if active_hud_log is not None:
            visible_chars = max(0, min(len(active_hud_log), int(active_hud_chars)))
            partial_log = active_hud_log[:visible_chars]
            color = get_log_color(active_hud_log)
            partial_lines = wrap_log(partial_log)
            for row_index, wrapped_line in enumerate(partial_lines):
                is_last_row = row_index == len(partial_lines) - 1
                display_rows.append((wrapped_line, color, is_last_row))

        # Calculate exactly how many wrapped rows fit. Then take the tail of the
        # stream. This is the auto-scroll behavior: as soon as new narration
        # reaches the bottom, old rows move upward automatically.
        top_y = 72
        bottom_y = h - 18
        line_height = 22
        visible_row_count = max(1, (bottom_y - top_y) // line_height)
        visible_rows = display_rows[-visible_row_count:]

        y_offset = top_y
        for line, text_color, is_typing_row in visible_rows:
            # A blinking cursor emphasizes that the current LLM narration is
            # being written live from left to right.
            rendered_line = line
            if is_typing_row and int(time.time() * 2) % 2 == 0:
                rendered_line += " |"

            cv2.putText(
                hud_panel,
                rendered_line,
                (15, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                text_color,
                1,
                cv2.LINE_AA,
            )
            y_offset += line_height

        # Show how many complete messages are still waiting behind the current
        # typewriter message without interrupting the narration itself.
        if pending_hud_logs:
            pending_text = f"queued agent messages: {len(pending_hud_logs)}"
            cv2.putText(
                hud_panel,
                pending_text,
                (hud_width - 225, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (135, 135, 135),
                1,
                cv2.LINE_AA,
            )

        # =========================================================================
        # DISPLAY THE COMPLETE APPLICATION WINDOW
        # =========================================================================

        # Show the combined camera + HUD screen.
        # The window contains:
        # LEFT: Camera, Face rectangle, Smile score
        # RIGHT: LLM/agent status messages
        cv2.imshow("System A - Autonomous Agentic HUD", combined_screen)
        # Wait 1 millisecond for keyboard input.
        # If the user presses 'q', exit the while loop
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    # =========================================================================
    # CLEANUP
    # =========================================================================

    # Release the webcam.
    cap.release()
    # Close all OpenCV windows.
    cv2.destroyAllWindows()
    # Release MediaPipe resources.
    landmarker.close()


# ============================================================================
# PROGRAM ENTRY POINT
# ============================================================================

# This condition is True only when this file is run directly.
# For example: python smile_detector_llm_hud.py
# If another Python file imports this file, main() will not automatically execute.
if __name__ == "__main__":
    main()