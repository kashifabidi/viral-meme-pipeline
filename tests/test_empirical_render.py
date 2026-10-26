"""Empirical diagnostic harness for video assembly and MoviePy pipeline."""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import traceback
from video_editor import assemble_meme_edit_with_freeze_and_caption

def main():
    test_in = "downloads/35KDnej1hlI.mp4"
    test_out = "output/test_empirical_out.mp4"
    print(f"Testing assemble with {test_in} -> {test_out}")
    try:
        assemble_meme_edit_with_freeze_and_caption(
            test_in,
            climax_timestamp=41.275,
            caption_text="Empirical Challenger Test",
            record_scratch_sfx="assets/sfx/windows_error.mp3",
            impact_sfx="assets/sfx/metal_pipe.mp3",
            overlay_image_path="assets/overlays/moyai.png",
            output_path=test_out,
        )
        print("SUCCESS! Output generated:", test_out, os.path.getsize(test_out))
    except Exception as exc:
        print("FAILED WITH EXCEPTION:", exc)
        traceback.print_exc()

if __name__ == "__main__":
    main()
