#pragma once

#include "Commandlets/Commandlet.h"

#include "BevySourceReaderCommandlet.generated.h"

UCLASS()
class BEVYSOURCEREADER_API UBevySourceReaderCommandlet : public UCommandlet
{
    GENERATED_BODY()

public:
    UBevySourceReaderCommandlet();

    virtual int32 Main(const FString& Params) override;
};
