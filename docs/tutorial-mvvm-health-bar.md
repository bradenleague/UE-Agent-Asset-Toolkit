# UE5 MVVM Tutorial: Build a Health Bar with C++ and Blueprints

> **Engine version:** Unreal Engine 5.3+
> **Difficulty:** Intermediate (assumes basic UE C++ and UMG knowledge)
> **What you'll build:** A health bar widget driven by a C++ viewmodel using Epic's built-in ModelViewViewModel plugin — no manual delegate wiring, no Tick updates, no spaghetti.

---

## Table of Contents

1. [Why MVVM?](#1-why-mvvm)
2. [How Lyra Does It Today (The Problem)](#2-how-lyra-does-it-today-the-problem)
3. [Architecture Overview](#3-architecture-overview)
4. [Enable the Plugin](#4-enable-the-plugin)
5. [Create the Viewmodel (C++)](#5-create-the-viewmodel-c)
6. [Create the Widget (Blueprint)](#6-create-the-widget-blueprint)
7. [Wire Up Bindings in the Editor](#7-wire-up-bindings-in-the-editor)
8. [Connect the Viewmodel to Gameplay Code](#8-connect-the-viewmodel-to-gameplay-code)
9. [Conversion Functions](#9-conversion-functions)
10. [Binding Modes and Execution Modes](#10-binding-modes-and-execution-modes)
11. [Advanced: The Global Viewmodel Collection](#11-advanced-the-global-viewmodel-collection)
12. [Advanced: Custom Resolvers](#12-advanced-custom-resolvers)
13. [What We Gained](#13-what-we-gained)
14. [Reference](#14-reference)

---

## 1. Why MVVM?

If you've built HUD widgets in Unreal, you've written code like this:

```
Widget constructs → finds the player pawn → gets the health component →
binds OnHealthChanged → in the callback, grabs the new value →
finds the progress bar → sets its percent → oh wait, the pawn might be
null during respawn → add a null check → wait, which tick does this
fire on? → add a delay...
```

This is **event spaghetti**. The widget knows too much about where the data lives, how to find it, and when it changes. Every new field (ammo, shields, stamina) means more delegate bindings, more null checks, more Blueprint wiring.

**MVVM** (Model-View-ViewModel) fixes this by inserting a **viewmodel** between your game data and your widget:

```
Model (HealthComponent)  →  ViewModel (HealthBarVM)  →  View (W_HealthBar)
      game logic                 shaped for UI              just displays
```

- The **Model** is your game data. It doesn't know about UI.
- The **ViewModel** is a simple C++ object that holds exactly the data the widget needs, in the shape the widget needs it. It notifies the widget when values change.
- The **View** (the widget) just binds its visual properties to viewmodel fields. It never talks to the game directly.

UE5 ships a built-in MVVM plugin (`ModelViewViewModel`) that handles the binding, notification, and compilation. You write the viewmodel in C++, then wire bindings visually in the Widget Blueprint editor. No code in the widget. No manual delegates.

---

## 2. How Lyra Does It Today (The Problem)

Let's look at how Lyra's `W_Healthbar` currently works — it's a textbook example of the pattern MVVM replaces.

**The data chain:**

```
ULyraHealthSet (GAS Attribute)
    → ULyraHealthComponent::OnHealthChanged (multicast delegate)
        → W_Healthbar "Health Changed" event (Blueprint)
            → UpdateHealthbar (manually sets bar fill, glow, number text...)
```

Here's the actual `ULyraHealthComponent` header (simplified):

```cpp
// LyraHealthComponent.h
UCLASS(Blueprintable)
class ULyraHealthComponent : public UGameFrameworkComponent
{
    GENERATED_BODY()

public:
    UFUNCTION(BlueprintCallable)
    float GetHealth() const;

    UFUNCTION(BlueprintCallable)
    float GetMaxHealth() const;

    UFUNCTION(BlueprintCallable)
    float GetHealthNormalized() const;

    // Widget has to bind to these manually
    UPROPERTY(BlueprintAssignable)
    FLyraHealth_AttributeChanged OnHealthChanged;

    UPROPERTY(BlueprintAssignable)
    FLyraHealth_DeathEvent OnDeathStarted;
};
```

And the widget (`W_Healthbar`) handles all of this in Blueprint event graphs — `Construct`, `OnPosessedPawnChanged`, `Health Changed`, `EventOnEliminated`, `UpdateHealthbar`, `SetDynamicMaterials`, `ResetAnimatedState`... seven custom functions just to display a health bar.

**The problems:**

1. **The widget must know how to find the health component.** It walks the pawn, checks for null, subscribes to delegates.
2. **Every new field = more wiring.** Add shields? Add another delegate, another callback, another update function.
3. **Lifecycle bugs.** What if the pawn isn't possessed yet? What if the health component initializes after the widget? The widget needs defensive code for all of this.
4. **Not reusable.** This widget is welded to `ULyraHealthComponent`. You can't point it at a different data source without rewriting the Blueprint.

---

## 3. Architecture Overview

Here's what we're going to build:

```
┌──────────────────────────────────────────────────────────────┐
│                        MODEL                                  │
│  ULyraHealthComponent                                        │
│    - Health, MaxHealth (from GAS)                            │
│    - OnHealthChanged delegate                                │
└──────────────────┬───────────────────────────────────────────┘
                   │ C++ code pushes values
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                      VIEWMODEL                                │
│  UHealthBarViewModel : UMVVMViewModelBase                    │
│    - CurrentHealth (float, FieldNotify)                      │
│    - MaxHealth (float, FieldNotify)                          │
│    - HealthPercent (float, FieldNotify)                      │
│    - bIsAlive (bool, FieldNotify)                            │
│    - HealthText (FText, FieldNotify)                         │
└──────────────────┬───────────────────────────────────────────┘
                   │ compiled bindings (automatic)
                   ▼
┌──────────────────────────────────────────────────────────────┐
│                         VIEW                                  │
│  W_HealthBar (Widget Blueprint)                              │
│    - ProgressBar.Percent ← HealthPercent                     │
│    - HealthText.Text ← HealthText                            │
│    - Visibility ← bIsAlive                                   │
└──────────────────────────────────────────────────────────────┘
```

The widget has **zero logic**. It just declares which viewmodel field maps to which widget property. The MVVM plugin compiles these bindings at editor time and executes them at runtime with zero allocations.

---

## 4. Enable the Plugin

The MVVM plugin ships with UE5 but isn't enabled by default.

**In the Editor:**

1. Edit → Plugins
2. Search for "Model View Viewmodel"
3. Enable the **ModelViewViewModel** plugin
4. Restart the editor

**Or in your `.uproject` file:**

```json
{
    "Plugins": [
        {
            "Name": "ModelViewViewModel",
            "Enabled": true
        }
    ]
}
```

**In your module's `Build.cs`:**

```csharp
PublicDependencyModuleNames.AddRange(new string[]
{
    "ModelViewViewModel",
    "FieldNotification",   // for the FieldNotify macros
    // ... your other modules
});
```

---

## 5. Create the Viewmodel (C++)

This is the core of the tutorial. We're going to create a viewmodel that holds exactly the data our health bar needs.

### 5.1 The Header

```cpp
// HealthBarViewModel.h

#pragma once

#include "MVVMViewModelBase.h"
#include "HealthBarViewModel.generated.h"

UCLASS(BlueprintType)
class YOURGAME_API UHealthBarViewModel : public UMVVMViewModelBase
{
    GENERATED_BODY()

public:
    // --- Observable Properties ---

    UPROPERTY(BlueprintReadWrite, FieldNotify, Setter, Getter, Category = "Health")
    float CurrentHealth;

    UPROPERTY(BlueprintReadWrite, FieldNotify, Setter, Getter, Category = "Health")
    float MaxHealth;

    UPROPERTY(BlueprintReadOnly, FieldNotify, Getter, Category = "Health")
    float HealthPercent;

    UPROPERTY(BlueprintReadOnly, FieldNotify, Getter, Category = "Health")
    bool bIsAlive;

    UPROPERTY(BlueprintReadOnly, FieldNotify, Getter, Category = "Health")
    FText HealthText;

    // --- Getters ---

    float GetCurrentHealth() const { return CurrentHealth; }
    float GetMaxHealth() const { return MaxHealth; }
    float GetHealthPercent() const { return HealthPercent; }
    bool GetbIsAlive() const { return bIsAlive; }
    FText GetHealthText() const { return HealthText; }

    // --- Setters ---

    void SetCurrentHealth(float NewHealth);
    void SetMaxHealth(float NewMaxHealth);

    // Convenience: set both and recompute derived fields in one call
    void SetHealth(float NewHealth, float NewMaxHealth);
};
```

**Key things to notice:**

- `FieldNotify` on the `UPROPERTY` — this tells the MVVM plugin to generate notification infrastructure for this field. The Blueprint compiler (or UHT for C++) creates the field descriptor automatically.
- `Setter` and `Getter` — these tell UHT to use your custom setter/getter functions instead of direct field access. The naming convention is `Set<PropertyName>` and `Get<PropertyName>`.
- `HealthPercent`, `bIsAlive`, and `HealthText` are **derived fields** — they're computed from `CurrentHealth` and `MaxHealth`. We make them `BlueprintReadOnly` because only the viewmodel should set them.

### 5.2 The Implementation

```cpp
// HealthBarViewModel.cpp

#include "HealthBarViewModel.h"

void UHealthBarViewModel::SetCurrentHealth(float NewHealth)
{
    // UE_MVVM_SET_PROPERTY_VALUE checks if the value actually changed.
    // If it did, it assigns the new value and broadcasts the change.
    // If it didn't, it's a no-op. No unnecessary UI updates.
    if (UE_MVVM_SET_PROPERTY_VALUE(CurrentHealth, NewHealth))
    {
        // CurrentHealth changed — recompute derived fields
        const float NewPercent = (MaxHealth > 0.f) ? (CurrentHealth / MaxHealth) : 0.f;
        UE_MVVM_SET_PROPERTY_VALUE(HealthPercent, NewPercent);

        const bool bNewIsAlive = CurrentHealth > 0.f;
        UE_MVVM_SET_PROPERTY_VALUE(bIsAlive, bNewIsAlive);

        UE_MVVM_SET_PROPERTY_VALUE(HealthText,
            FText::AsNumber(FMath::CeilToInt(CurrentHealth)));
    }
}

void UHealthBarViewModel::SetMaxHealth(float NewMaxHealth)
{
    if (UE_MVVM_SET_PROPERTY_VALUE(MaxHealth, NewMaxHealth))
    {
        // MaxHealth changed — recompute percent
        const float NewPercent = (MaxHealth > 0.f) ? (CurrentHealth / MaxHealth) : 0.f;
        UE_MVVM_SET_PROPERTY_VALUE(HealthPercent, NewPercent);
    }
}

void UHealthBarViewModel::SetHealth(float NewHealth, float NewMaxHealth)
{
    // Batch update: set both values, recompute once
    bool bChanged = false;
    bChanged |= UE_MVVM_SET_PROPERTY_VALUE(CurrentHealth, NewHealth);
    bChanged |= UE_MVVM_SET_PROPERTY_VALUE(MaxHealth, NewMaxHealth);

    if (bChanged)
    {
        const float NewPercent = (NewMaxHealth > 0.f) ? (NewHealth / NewMaxHealth) : 0.f;
        UE_MVVM_SET_PROPERTY_VALUE(HealthPercent, NewPercent);

        const bool bNewIsAlive = NewHealth > 0.f;
        UE_MVVM_SET_PROPERTY_VALUE(bIsAlive, bNewIsAlive);

        UE_MVVM_SET_PROPERTY_VALUE(HealthText,
            FText::AsNumber(FMath::CeilToInt(NewHealth)));
    }
}
```

### 5.3 Understanding `UE_MVVM_SET_PROPERTY_VALUE`

This macro expands to:

```cpp
SetPropertyValue(CurrentHealth, NewHealth,
    ThisClass::FFieldNotificationClassDescriptor::CurrentHealth)
```

Which does:
1. Compares `CurrentHealth == NewHealth`
2. If equal → returns `false` (no change, no broadcast)
3. If different → assigns the value, calls `BroadcastFieldValueChanged`, returns `true`

This is the entire notification system. No delegate registration in the viewmodel, no manual `OnPropertyChanged` calls, no event dispatchers. The MVVM plugin's `UMVVMView` (attached to the widget instance at runtime) listens for these broadcasts and executes the compiled bindings automatically.

There are three macros, use the right one:

| Macro | Use when... |
|---|---|
| `UE_MVVM_SET_PROPERTY_VALUE(Field, Value)` | Normal case. Checks equality, sets, notifies. |
| `UE_MVVM_BROADCAST_FIELD_VALUE_CHANGED(Field)` | You've already set the value yourself and just need to notify. |
| `UE_MVVM_SET_PROPERTY_VALUE_INLINE(Field, Value)` | For bitfield bools that can't be passed by reference. |

---

## 6. Create the Widget (Blueprint)

Now create a Widget Blueprint that will display the health bar. We're keeping this simple to focus on MVVM concepts.

### 6.1 Widget Hierarchy

Create a new Widget Blueprint (e.g., `WBP_HealthBar`). Build this hierarchy:

```
[CanvasPanel] Root
  └─ [HorizontalBox]
       ├─ [ProgressBar] "HealthBar"
       │     Percent: (will be bound to ViewModel)
       │     Fill Color: Green
       └─ [TextBlock] "HealthNumber"
             Text: (will be bound to ViewModel)
```

That's it. No event graph code. No custom functions. Just layout.

### 6.2 Add the Viewmodel

In the Widget Blueprint editor, look for the **Viewmodels** panel (usually next to the Variables panel in the My Blueprint tab). If you don't see it:

1. Go to Window → Viewmodels (or it might appear as "MVVM" in some versions)
2. Click the **+** button to add a new viewmodel
3. Select your `UHealthBarViewModel` class
4. Name it (e.g., `HealthVM`)
5. Set the **Creation Type** (see below)

**Creation Type options:**

| Type | When to use |
|---|---|
| **Create Instance** | The widget creates and owns the viewmodel. Simplest option. |
| **Manual** | You'll call `SetViewModel` from code to provide it. Use when the viewmodel is shared or created elsewhere. |
| **Global Collection** | Fetched from `UMVVMGameSubsystem` by name. Good for singleton-style viewmodels. |
| **Property Path** | Resolved from a property chain (e.g., `GetOwningPlayer.GetPlayerState.MyVM`). |
| **Resolver** | Custom factory class. Most flexible. |

For this tutorial, start with **Manual** — we'll set the viewmodel from gameplay code in step 8.

---

## 7. Wire Up Bindings in the Editor

This is where MVVM shines. In the Widget Blueprint editor:

### 7.1 Open the Bindings Panel

Look for the **Bindings** tab in the bottom panel (next to the Compiler Results tab). This is where you connect viewmodel fields to widget properties.

### 7.2 Add Bindings

Click **+ Add Binding** and create these:

**Binding 1: Health bar fill**
```
Source:      HealthVM → HealthPercent
Destination: HealthBar → Percent
Mode:        One Way To Destination
```

**Binding 2: Health number text**
```
Source:      HealthVM → HealthText
Destination: HealthNumber → Text
Mode:        One Way To Destination
```

**Binding 3: Visibility on death (optional)**
```
Source:      HealthVM → bIsAlive
Destination: Root → Visibility
Mode:        One Way To Destination
Conversion:  Bool To ESlateVisibility (see Section 9)
```

That's the entire widget logic. Three bindings, zero Blueprint nodes.

### 7.3 What Happens at Compile Time

When you compile the Widget Blueprint, the MVVM editor extension:

1. Validates all binding paths (source exists, destination exists, types are compatible)
2. Compiles them into an `FMVVMCompiledBindingLibrary` — a flat array of field path indices
3. Stores the compiled data in `UMVVMViewClass` (shared across all instances of this widget class)

At runtime, when a field changes:
1. The viewmodel calls `BroadcastFieldValueChanged(HealthPercent)`
2. `UMVVMView` (per-widget-instance) receives the notification
3. It looks up the compiled binding for `HealthPercent`
4. Walks the property path and copies the value directly — no Blueprint VM, no allocation

---

## 8. Connect the Viewmodel to Gameplay Code

The viewmodel exists and the widget has bindings, but something needs to feed data into the viewmodel. This is where we connect to the game's health system.

### 8.1 Option A: From a HUD/Controller class

The simplest approach — create the viewmodel, set it on the widget, and feed it data:

```cpp
// In your HUD class or wherever you create the health bar widget

void AMyHUD::CreateHealthBar()
{
    // Create the widget
    HealthBarWidget = CreateWidget<UUserWidget>(GetOwningPlayerController(),
        HealthBarWidgetClass);

    // Create the viewmodel
    HealthBarVM = NewObject<UHealthBarViewModel>(this);

    // Set it on the widget using the MVVM subsystem
    if (UMVVMSubsystem* MVVMSubsystem = GEngine->GetEngineSubsystem<UMVVMSubsystem>())
    {
        if (UMVVMView* View = MVVMSubsystem->GetViewFromUserWidget(HealthBarWidget))
        {
            View->SetViewModel(TEXT("HealthVM"), HealthBarVM);
        }
    }

    // Subscribe to the health component's changes
    if (ULyraHealthComponent* HealthComp = /* find it on the pawn */)
    {
        HealthComp->OnHealthChanged.AddDynamic(this, &AMyHUD::HandleHealthChanged);
    }

    HealthBarWidget->AddToViewport();
}

void AMyHUD::HandleHealthChanged(ULyraHealthComponent* HealthComp,
    float OldValue, float NewValue, AActor* Instigator)
{
    // Just push the values into the viewmodel. That's it.
    // The viewmodel handles the notification, the bindings handle the widget update.
    HealthBarVM->SetHealth(NewValue, HealthComp->GetMaxHealth());
}
```

### 8.2 Option B: Using a Custom Resolver

For a cleaner architecture, create a resolver that automatically finds the health component:

```cpp
// HealthBarViewModelResolver.h

#pragma once

#include "View/MVVMViewModelContextResolver.h"
#include "HealthBarViewModelResolver.generated.h"

UCLASS(BlueprintType)
class YOURGAME_API UHealthBarViewModelResolver : public UMVVMViewModelContextResolver
{
    GENERATED_BODY()

public:
    virtual UObject* CreateInstance(
        const UClass* ExpectedType,
        const UUserWidget* UserWidget,
        const UMVVMView* View) const override;

    virtual void DestroyInstance(
        const UObject* ViewModel,
        const UMVVMView* View) const override;
};
```

```cpp
// HealthBarViewModelResolver.cpp

#include "HealthBarViewModelResolver.h"
#include "HealthBarViewModel.h"
#include "Character/LyraHealthComponent.h"
#include "GameFramework/PlayerController.h"

UObject* UHealthBarViewModelResolver::CreateInstance(
    const UClass* ExpectedType,
    const UUserWidget* UserWidget,
    const UMVVMView* View) const
{
    APlayerController* PC = UserWidget->GetOwningPlayer();
    if (!PC || !PC->GetPawn())
    {
        return nullptr;
    }

    UHealthBarViewModel* VM = NewObject<UHealthBarViewModel>(
        PC->GetPawn());

    // Find the health component and subscribe
    if (ULyraHealthComponent* HealthComp =
        ULyraHealthComponent::FindHealthComponent(PC->GetPawn()))
    {
        // Initial values
        VM->SetHealth(HealthComp->GetHealth(), HealthComp->GetMaxHealth());

        // Subscribe to future changes
        HealthComp->OnHealthChanged.AddLambda(
            [WeakVM = TWeakObjectPtr<UHealthBarViewModel>(VM)]
            (ULyraHealthComponent* HC, float OldVal, float NewVal, AActor*)
            {
                if (UHealthBarViewModel* StrongVM = WeakVM.Get())
                {
                    StrongVM->SetHealth(NewVal, HC->GetMaxHealth());
                }
            });
    }

    return VM;
}

void UHealthBarViewModelResolver::DestroyInstance(
    const UObject* ViewModel, const UMVVMView* View) const
{
    // The viewmodel will be GC'd. If you registered any non-UObject
    // delegates, clean them up here.
}
```

Then in the Widget Blueprint's Viewmodel panel, set the Creation Type to **Resolver** and pick your `UHealthBarViewModelResolver`.

### 8.3 Option C: Blueprint-only (SetViewModelByClass)

If you prefer to keep it in Blueprints, the MVVM plugin provides a utility:

1. In the widget's Event Graph, on `Construct`:
2. Call **Set Viewmodel By Class** (from `UMVVMBlueprintLibrary`)
3. This assigns an already-created viewmodel instance to the widget

---

## 9. Conversion Functions

Sometimes the viewmodel property type doesn't directly match the widget property type. For example, you might want to bind a `bool` to `ESlateVisibility`, or a `float` to `FText`.

### 9.1 Built-in Conversions

The MVVM plugin automatically handles:
- `float` ↔ `double` widening
- Integer type promotions
- Basic property compatibility checks

### 9.2 Custom Conversion Functions

Write a static `UFUNCTION` that takes one input and returns one output:

```cpp
UCLASS()
class UHealthBarConversions : public UBlueprintFunctionLibrary
{
    GENERATED_BODY()

public:
    // Bool → Visibility (common pattern)
    UFUNCTION(BlueprintPure, Category = "MVVM|Conversions")
    static ESlateVisibility BoolToVisibility(bool bIsVisible)
    {
        return bIsVisible
            ? ESlateVisibility::HitTestInvisible
            : ESlateVisibility::Collapsed;
    }

    // Float → Formatted health text ("75 / 100")
    UFUNCTION(BlueprintPure, Category = "MVVM|Conversions")
    static FText HealthToFormattedText(float HealthPercent)
    {
        return FText::Format(
            NSLOCTEXT("Health", "PercentFormat", "{0}%"),
            FText::AsNumber(FMath::RoundToInt(HealthPercent * 100.f)));
    }

    // Float → Color (green → yellow → red)
    UFUNCTION(BlueprintPure, Category = "MVVM|Conversions")
    static FLinearColor HealthPercentToColor(float Percent)
    {
        // Green at 100%, yellow at 50%, red at 0%
        if (Percent > 0.5f)
        {
            return FLinearColor::LerpUsingHSV(
                FLinearColor::Yellow, FLinearColor::Green,
                (Percent - 0.5f) * 2.f);
        }
        return FLinearColor::LerpUsingHSV(
            FLinearColor::Red, FLinearColor::Yellow,
            Percent * 2.f);
        }
};
```

In the Bindings panel, when you add a binding with mismatched types, the editor will offer to insert a conversion function. Select yours from the list.

**Requirements for a valid conversion function:**
- Must be a `UFUNCTION` (static or member)
- Must have exactly one input parameter and one return value (for "simple" conversions)
- Must be `BlueprintCallable` or `BlueprintPure`

---

## 10. Binding Modes and Execution Modes

### Binding Modes

| Mode | Direction | Use case |
|---|---|---|
| `OneTimeToDestination` | VM → Widget (once at init) | Static labels, configuration values |
| `OneWayToDestination` | VM → Widget (live) | Health bars, score displays, any read-only display |
| `TwoWay` | VM ↔ Widget | Text input fields, sliders the player can drag |
| `OneWayToSource` | Widget → VM | Capturing user input back to the viewmodel |

For our health bar, everything is `OneWayToDestination` — data flows from the viewmodel to the widget.

### Execution Modes

| Mode | When bindings execute | Use case |
|---|---|---|
| `Immediate` | Instantly when the field changes | Low-latency updates (health, ammo) |
| `Delayed` | Deferred to end-of-frame, before draw | Multiple fields changing together (batch-friendly) |
| `Tick` | Every frame | Polling-style, avoid if possible |
| `DelayedWhenSharedElseImmediate` | Auto: delayed if multiple sources, immediate otherwise | Default safe choice |

For health, `Immediate` is fine — you want the bar to update the same frame damage is applied.

---

## 11. Advanced: The Global Viewmodel Collection

For data that many widgets need (player stats, game state, settings), you can register a viewmodel in the **Global Viewmodel Collection** instead of wiring each widget individually.

```cpp
// Register a viewmodel globally (e.g., from your GameMode or GameState)
void AMyGameMode::BeginPlay()
{
    Super::BeginPlay();

    UMVVMGameSubsystem* Subsystem = GetGameInstance()->GetSubsystem<UMVVMGameSubsystem>();
    if (UMVVMViewModelCollectionObject* Collection = Subsystem->GetViewModelCollection())
    {
        FMVVMViewModelContext Context;
        Context.ContextName = TEXT("PlayerHealth");
        Context.ContextClass = UHealthBarViewModel::StaticClass();

        UHealthBarViewModel* VM = NewObject<UHealthBarViewModel>(this);
        Collection->AddViewModelInstance(Context, VM);
    }
}
```

Any widget with a viewmodel slot set to **Global Collection** + name `"PlayerHealth"` will automatically receive this viewmodel. No manual wiring. Multiple widgets can share the same viewmodel instance.

---

## 12. Advanced: Custom Resolvers

For complex cases where the viewmodel needs to pull from game-specific systems (ability system, inventory, etc.), create a `UMVVMViewModelContextResolver` subclass:

```cpp
UCLASS(BlueprintType, EditInlineNew)
class UPawnHealthResolver : public UMVVMViewModelContextResolver
{
    GENERATED_BODY()

    virtual UObject* CreateInstance(const UClass* ExpectedType,
        const UUserWidget* UserWidget, const UMVVMView* View) const override
    {
        // Your logic to find the health component, create the VM,
        // and subscribe to updates (see Section 8.2)
    }
};
```

Set the Creation Type to **Resolver** in the widget's Viewmodel panel and assign your resolver class. The widget doesn't need to know anything about pawns, health components, or ability systems — the resolver handles it all.

---

## 13. What We Gained

Let's compare before and after:

### Before (Lyra's Event-Driven Approach)

| Aspect | Reality |
|---|---|
| **W_Healthbar Blueprint** | 7 custom functions, 13 widgets, manual delegate wiring |
| **Data flow** | Widget → FindHealthComponent → BindDelegate → Callback → UpdateBar |
| **Lifecycle** | Widget must handle: pawn not ready, respawn, null component |
| **New field (shields)** | New delegate binding, new callback, new update function |
| **Reuse** | Hardwired to `ULyraHealthComponent` |

### After (MVVM)

| Aspect | Reality |
|---|---|
| **WBP_HealthBar Blueprint** | 0 functions, pure layout, 3 bindings |
| **Data flow** | HealthComponent → ViewModel.SetHealth() → binding → widget |
| **Lifecycle** | Resolver or caller handles sourcing; widget just needs a viewmodel |
| **New field (shields)** | Add `UPROPERTY(FieldNotify)` to viewmodel + 1 binding in editor |
| **Reuse** | Any health source can feed the same viewmodel class |

The widget went from **7 Blueprint functions** to **zero**. Adding a new field is a `UPROPERTY` + a binding, not a new event chain.

---

## 14. Reference

### Key Source Files (Engine)

```
Engine/Plugins/Runtime/ModelViewViewModel/
  Source/ModelViewViewModel/Public/
    MVVMViewModelBase.h              — base class you extend
    MVVMGameSubsystem.h              — global viewmodel collection
    MVVMSubsystem.h                  — GetViewFromUserWidget, validation
    View/MVVMView.h                  — per-widget-instance runtime
    View/MVVMViewClass.h             — per-widget-class compiled data
    View/MVVMViewModelContextResolver.h — custom resolver base
    Bindings/MVVMCompiledBindingLibrary.h — binding execution engine
    Types/MVVMBindingMode.h          — OneWay, TwoWay, etc.
    Types/MVVMExecutionMode.h        — Immediate, Delayed, Tick

Engine/Source/Runtime/FieldNotification/Public/
    FieldNotificationDeclaration.h   — UE_FIELD_NOTIFICATION_* macros
    INotifyFieldValueChanged.h       — the core interface
```

### Key Macros

| Macro | Purpose |
|---|---|
| `UE_MVVM_SET_PROPERTY_VALUE(Field, Value)` | Set + notify if changed |
| `UE_MVVM_BROADCAST_FIELD_VALUE_CHANGED(Field)` | Notify only (you set the value already) |
| `UE_MVVM_SET_PROPERTY_VALUE_INLINE(Field, Value)` | For bitfield bools |

### Viewmodel Creation Types

| Type | Use case |
|---|---|
| `Manual` | Caller provides the VM via `SetViewModel` |
| `CreateInstance` | Widget `NewObject`s its own VM on construct |
| `GlobalViewModelCollection` | Fetched from `UMVVMGameSubsystem` by key |
| `PropertyPath` | Resolved from a function/property chain |
| `Resolver` | Custom `UMVVMViewModelContextResolver` subclass |

### UPROPERTY Specifiers for MVVM

| Specifier | Purpose |
|---|---|
| `FieldNotify` | Makes the property observable by the MVVM binding system |
| `Setter` | Use a custom setter (naming convention: `Set<PropertyName>`) |
| `Getter` | Use a custom getter (naming convention: `Get<PropertyName>`) |

### Class Metadata

| Meta | Purpose |
|---|---|
| `MVVMAllowedContextCreationType="Manual\|CreateInstance"` | Restrict how this VM can be instantiated |
| `MVVMDisallowedContextCreationType="..."` | Inverse: block specific creation types |

---

## Next Steps

This tutorial covered a single health bar. From here you could:

- **Player stats panel** — add `Ammo`, `Score`, `ShieldPercent` to the same viewmodel
- **Inventory slot** — a viewmodel per item with `Icon`, `Count`, `Rarity`, `bIsEquipped`
- **Scoreboard row** — bind to a `UMVVMViewModelBase` subclass per player, use ListView extension for lists
- **Settings menu** — `TwoWay` bindings for sliders and checkboxes that write back to the viewmodel

The MVVM plugin also ships a **ListView extension** (`UMVVMViewListViewBaseClassExtension`) that automatically sets the viewmodel on each entry widget when it's generated — perfect for inventory grids and scoreboards.

---

*Written from the UE 5.7 engine source. The MVVM plugin is marked Beta — API details may shift in future versions.*
