import ast
import tkinter as tk

from ib_insync import IB, util
from ib_insync.contract import (
    Bag, Bond, CFD, Commodity, ContFuture, Contract, Forex, Future,
    FuturesOption, Index, MutualFund, Option, Stock, Warrant)

_CONTRACT_TYPES = {
    cls.__name__: cls for cls in (
        Bag, Bond, CFD, Commodity, ContFuture, Contract, Forex, Future,
        FuturesOption, Index, MutualFund, Option, Stock, Warrant)
}


def parse_contract(text: str) -> Contract:
    """Safely parse a Contract call like Stock('TSLA', 'SMART', 'USD')."""
    tree = ast.parse(text.strip(), mode='eval')
    if (not isinstance(tree.body, ast.Call)
            or not isinstance(tree.body.func, ast.Name)):
        raise ValueError(f'Invalid contract expression: {text!r}')
    cls = _CONTRACT_TYPES.get(tree.body.func.id)
    if cls is None:
        raise ValueError(f'Unknown contract type: {tree.body.func.id!r}')
    args = [ast.literal_eval(arg) for arg in tree.body.args]
    kwargs = {}
    for kw in tree.body.keywords:
        if kw.arg is None:
            raise ValueError('**kwargs unpacking is not allowed')
        kwargs[kw.arg] = ast.literal_eval(kw.value)
    return cls(*args, **kwargs)


util.patchAsyncio()


class TkApp:
    """
    Example of integrating with Tkinter.
    """
    def __init__(self):
        self.ib = IB().connect()
        self.root = tk.Tk()
        self.root.protocol('WM_DELETE_WINDOW', self._onDeleteWindow)
        self.entry = tk.Entry(self.root, width=50)
        self.entry.insert(0, "Stock('TSLA', 'SMART', 'USD')")
        self.entry.grid()
        self.button = tk.Button(
            self.root, text='Get details', command=self.onButtonClick)
        self.button.grid()
        self.text = tk.Text(self.root)
        self.text.grid()
        self.loop = util.getLoop()

    def onButtonClick(self):
        contract = parse_contract(self.entry.get())
        cds = self.ib.reqContractDetails(contract)
        self.text.delete(1.0, tk.END)
        self.text.insert(tk.END, str(cds))

    def run(self):
        self._onTimeout()
        self.loop.run_forever()

    def _onTimeout(self):
        self.root.update()
        self.loop.call_later(0.03, self._onTimeout)

    def _onDeleteWindow(self):
        self.loop.stop()


app = TkApp()
app.run()
