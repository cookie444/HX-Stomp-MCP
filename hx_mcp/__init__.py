"""hx_mcp - an MCP server that controls a Line 6 HX Stomp over its USB vendor protocol.

This package speaks the same editor protocol HX Edit uses, on the vendor
control interface (USB Class 0xFF, MI_00). It is read-mostly and deliberately
never touches firmware/DFU.

Hard rules baked into the design (see README):
  * never send firmware/DFU messages;
  * always drain unsolicited device notifications (a backed-up queue wedges the
    device);
  * never issue a USB reset (the unit leaves the bus and does not return);
  * back up before writes.
"""

__version__ = "0.1.0"

VENDOR_ID = 0x0E41
PRODUCT_IDS = {
    0x4246: "HX Stomp",
    0x4253: "HX Stomp XL",
    0x4248: "Helix Floor",
    0x4245: "HX Effects",
}
