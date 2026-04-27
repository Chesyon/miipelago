from typing import Dict, Optional
import asyncio
import traceback
from BaseClasses import ItemClassification
from CommonClient import ClientCommandProcessor, CommonContext, get_base_parser, gui_enabled, logger, server_loop
from Patch import create_rom_file
from .citra import CitraInterface, CitraException
from .triple import TripleInterface, TripleException

citra = CitraInterface()
triple = TripleInterface()
triple_addr = ""

class MiitopiaCommandProcessor(ClientCommandProcessor):
    def _cmd_3ds(self, address):
        """Connect to a real 3DS"""
        global triple_addr
        if triple_addr == "":
            triple_addr = address
        else:
            self.output("Already connected to a 3DS")
    
    def _cmd_3dsdisconnect(self):
        """Disconnect from a 3DS"""
        global triple_addr
        if triple_addr == "":
            self.output("Not currently connected to a 3DS")
        else:
            self.output(f"Disconnected from {triple_addr}.")
            triple_addr = ""
    
    def _cmd_3dstimeout(self, timeout):
        """Set timeout (in connection attempts) until giving up connection to 3DS (default 200000)"""
        global triple
        if triple.set_timeout(timeout):
            self.output(f"Successfully set timeout to {timeout} attempts")
        else:
            self.error(f"Invalid timeout: {timeout}")

class MiitopiaClientContext(CommonContext):
    command_processor = MiitopiaCommandProcessor
    game: Optional[str] = "Miitopia"
    items_handling: Optional[int] = 0b101 # receive remote items and starting inventory
    want_slot_data: bool = True

    interface = None
    interface_connected: bool
    server_connected: bool
    initial_delay: bool
    slot_data: Optional[Dict[str, any]]
    invalid: bool
    last_error: str
    show_citra_connect_message: bool
    show_triple_connected_message: bool

    def __init__(self, server_address: Optional[str], password: Optional[str]):
        super().__init__(server_address, password)
        self.interface_connected = False
        self.server_connected = False
        self.initial_delay = True
        self.slot_data = None
        self.course_flags = []
        self.ravio_scouted = False
        self.to_hint = []
        self.invalid = False
        self.last_error = ""
        self.show_citra_connect_message = True
        self.show_triple_connected_message = True

    def run_gui(self) -> None:
        from kvui import GameManager

        class MiitopiaManager(GameManager):
            base_title: str = "Miitopia Client"

        self.ui = MiitopiaManager(self)
        self.ui_task = asyncio.create_task(self.ui.async_run(), name="UI")
    
    def error(self, error: str) -> None:
        if error != self.last_error:
            logger.error(error)
            self.last_error = error
        self.invalid = True
    
    async def citra_connect(self) -> None:
        if self.show_citra_connect_message:
            logger.info("Connecting to emulator...")
        self.show_citra_connect_message = False
        self.interface_connected = False
        if not await self.interface.connect():
            await asyncio.sleep(1)
        else:
            self.interface_connected = True
            self.initial_delay = True
            if self.server_connected:
                logger.info("Emulator connected")
            else:
                logger.info("Emulator connected, but not yet connected to the multiworld")

    async def server_auth(self, password_requested: bool = False) -> None:
        if password_requested and not self.password:
            await super(MiitopiaClientContext, self).server_auth(password_requested)
        if not self.auth:
            logger.info("Connected to the multiworld, awaiting connection to emulator to authenticate with server")
        while not self.auth and not self.exit_event.is_set():
            await asyncio.sleep(1)
        await self.send_connect()

async def game_watcher(ctx: MiitopiaClientContext) -> None:
    global citra
    global triple
    global triple_addr
    ctx.interface = citra
    while not ctx.exit_event.is_set():
        try:
            ctx.invalid = False
            if not ctx.interface_connected:
                if triple_addr != "":
                    if await triple.connect(triple_addr):
                        if ctx.show_triple_connected_message:
                            logger.info("3DS connected!")
                        ctx.initial_delay = True
                        ctx.interface = triple
                        ctx.interface_connected = True
                        ctx.show_citra_connect_message = False
                        ctx.show_triple_connected_message = False
                    else:
                        logger.info("Couldn't connect to 3DS.")
                        ctx.interface_connected = False
                        triple.disconnect()
                        triple_addr = ""
                else:
                    triple.disconnect()
                    ctx.interface_connected = False
                    ctx.show_triple_connected_message = True
                    ctx.interface = citra
                    await ctx.citra_connect()
            else:
                if ctx.initial_delay:
                    delay = 1
                    if ctx.interface == triple:
                        delay = 5
                    await asyncio.sleep(delay)
                    ctx.initial_delay = False
                # TODO
        except CitraException as e:
            logger.error(e)
            logger.error(traceback.format_exc())
            ctx.interface_connected = False
            ctx.last_error = ""
            ctx.show_citra_connect_message = True
        except TripleException as e:
            if str(e) != "":
                logger.error(e)
                logger.error(traceback.format_exc())
            ctx.interface_connected = False
            ctx.last_error = ""
            ctx.show_citra_connect_message = True
            ctx.interface = citra
        except Exception as e:
            logger.error(e)
            logger.error(traceback.format_exc())
            await ctx.disconnect()
            ctx.interface_connected = False
            ctx.server_connected = False
            ctx.last_error = ""
            ctx.show_citra_connect_message = True
        await asyncio.sleep(0.25)

def launch(*launch_args) -> None:
    async def main():
        parser = get_base_parser()
        parser.add_argument("patch_file", default="", type=str, nargs="?", help="Path to an Archipelago patch file")
        args = parser.parse_args(launch_args)

        if args.patch_file != "":
            create_rom_file(args.patch_file)

        ctx = MiitopiaClientContext(args.connect, args.password)
        ctx.server_task = asyncio.create_task(server_loop(ctx), name="ServerLoop")

        if gui_enabled:
            ctx.run_gui()
        ctx.run_cli()

        watcher_task = asyncio.create_task(game_watcher(ctx), name="GameWatcher")

        try:
            await watcher_task
        except Exception as e:
            logger.error("".join(traceback.format_exception(e)))

        await ctx.exit_event.wait()
        await ctx.shutdown()

    import colorama
    colorama.init()
    asyncio.run(main())
    colorama.deinit()
