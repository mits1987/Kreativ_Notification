import kreativ_notification.kreativ_notification as m
import os
print("__path__:", m.__path__)
print("__file__:", m.__file__)
print("contents of __path__[0]:", os.listdir(m.__path__[0]))
print("doctype exists:", os.path.isdir(os.path.join(m.__path__[0], "doctype")))
