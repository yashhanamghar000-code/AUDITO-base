import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { useAuth } from "@/contexts/AuthContext";
import { useTheme } from "@/contexts/ThemeContext";
import { toast } from "sonner";
import { Moon, Sun, LogOut, Trash2 } from "lucide-react";
import { useNavigate } from "@tanstack/react-router";

export function SettingsDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const { user, updateProfile, logout } = useAuth();
  const { theme, setTheme } = useTheme();
  const navigate = useNavigate();
  const [name, setName] = useState(user?.name ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [currentPw, setCurrentPw] = useState("");
  const [newPw, setNewPw] = useState("");

  function saveProfile() {
    updateProfile({ name, email });
    toast.success("Profile updated");
  }

  function changePassword() {
    if (!newPw || !currentPw) return toast.error("Fill in both fields");
    setCurrentPw("");
    setNewPw("");
    toast.success("Password changed");
  }

  function deleteAccount() {
    logout();
    toast.success("Account deleted");
    navigate({ to: "/auth/register", replace: true });
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Manage your account and preferences.</DialogDescription>
        </DialogHeader>
        <Tabs defaultValue="profile" className="mt-2">
          <TabsList className="grid grid-cols-3">
            <TabsTrigger value="profile">Profile</TabsTrigger>
            <TabsTrigger value="theme">Theme</TabsTrigger>
            <TabsTrigger value="account">Account</TabsTrigger>
          </TabsList>

          <TabsContent value="profile" className="space-y-4 pt-4">
            <div className="space-y-1.5">
              <Label htmlFor="s-name">Full name</Label>
              <Input id="s-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="s-email">Email</Label>
              <Input id="s-email" value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <Button onClick={saveProfile}>Save changes</Button>

            <Separator />
            <div className="space-y-3">
              <div className="text-sm font-medium">Change password</div>
              <div className="space-y-1.5">
                <Label htmlFor="s-cpw">Current password</Label>
                <Input
                  id="s-cpw"
                  type="password"
                  value={currentPw}
                  onChange={(e) => setCurrentPw(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="s-npw">New password</Label>
                <Input
                  id="s-npw"
                  type="password"
                  value={newPw}
                  onChange={(e) => setNewPw(e.target.value)}
                />
              </div>
              <Button variant="outline" onClick={changePassword}>
                Update password
              </Button>
            </div>
          </TabsContent>

          <TabsContent value="theme" className="pt-4">
            <div className="grid grid-cols-2 gap-3">
              <ThemeCard
                active={theme === "light"}
                onClick={() => setTheme("light")}
                icon={<Sun className="h-5 w-5" />}
                label="Light"
              />
              <ThemeCard
                active={theme === "dark"}
                onClick={() => setTheme("dark")}
                icon={<Moon className="h-5 w-5" />}
                label="Dark"
              />
            </div>
          </TabsContent>

          <TabsContent value="account" className="space-y-3 pt-4">
            <Button
              variant="outline"
              className="w-full justify-start gap-2"
              onClick={() => {
                logout();
                navigate({ to: "/auth/login", replace: true });
              }}
            >
              <LogOut className="h-4 w-4" /> Log out
            </Button>
            <Button
              variant="destructive"
              className="w-full justify-start gap-2"
              onClick={deleteAccount}
            >
              <Trash2 className="h-4 w-4" /> Delete account
            </Button>
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function ThemeCard({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={
        "flex flex-col items-center gap-2 rounded-xl border p-4 transition-colors " +
        (active
          ? "border-foreground bg-accent"
          : "border-border hover:bg-accent/50")
      }
    >
      {icon}
      <span className="text-sm font-medium">{label}</span>
    </button>
  );
}
